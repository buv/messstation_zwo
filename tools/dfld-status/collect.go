// Datenquellen — alle externen Calls via os/exec (kein Docker-SDK,
// kein InfluxDB-Client als Dependency). Hält das Binary klein und
// matched 1:1 die Pfade aus der frueheren Bash-Implementierung.

package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"gopkg.in/yaml.v3"
)

const (
	pathDfldYml        = "/boot/firmware/dfld.yml"
	pathConnectorsDir  = "/opt/dfld/connectors"
	pathTsdb2httpState = "/opt/dfld/tsdb2http/last-tx.txt"
	pathFreezeMarker   = "/boot/firmware/dfld-freeze"
)

// === Konfig (dfld.yml) ===

type DfldConfig struct {
	Region      string `yaml:"dfld_region"`
	Station     string `yaml:"dfld_station"`
	StationCity string `yaml:"station_city"`
	LiveEnabled *bool  `yaml:"dfld_live_enabled"`
	Backfill    string `yaml:"dfld_backfill_interval"`
	OsmAPIKey   string `yaml:"osm_api_key"`
	MqttBroker  string `yaml:"mqtt_bridged_broker"`
	MqttTLS     string `yaml:"mqtt_bridged_tls"`
}

func loadConfig() DfldConfig {
	var c DfldConfig
	b, err := os.ReadFile(pathDfldYml)
	if err != nil {
		return c
	}
	_ = yaml.Unmarshal(b, &c)

	// Migration vom alten Schema: falls weder dfld_live_enabled noch
	// dfld_backfill_interval gesetzt sind, versuche das alte
	// dfld_tx_tier-Feld zu interpretieren. Pattern 1:1 aus
	// tools/dfld-config/dfld-config.sh.
	if c.LiveEnabled == nil && c.Backfill == "" {
		var legacy struct {
			TxTier string `yaml:"dfld_tx_tier"`
		}
		_ = yaml.Unmarshal(b, &legacy)
		t, f := true, false
		switch legacy.TxTier {
		case "live":
			c.LiveEnabled, c.Backfill = &t, "hourly"
		case "hourly":
			c.LiveEnabled, c.Backfill = &f, "hourly"
		case "daily":
			c.LiveEnabled, c.Backfill = &f, "daily"
		case "off":
			c.LiveEnabled, c.Backfill = &f, "off"
		}
	}
	// Defaults wenn weder altes noch neues Schema Werte hatte
	// (frische Pi ohne dfld-config-Save bekommt damit die gleichen
	// Defaults wie der Container-Stack zur Laufzeit verwendet).
	if c.LiveEnabled == nil {
		t := true
		c.LiveEnabled = &t
	}
	if c.Backfill == "" {
		c.Backfill = "hourly"
	}
	return c
}

func freezeActive() bool {
	_, err := os.Stat(pathFreezeMarker)
	return err == nil
}

func (c DfldConfig) StationID() string {
	if c.Region != "" && c.Station != "" {
		return c.Region + "-" + c.Station
	}
	return "<unconfigured>"
}

// === exec-Helper ===

// run führt cmd mit Timeout aus und gibt getrimmte stdout zurück.
// Wir loggen Fehler nicht — die UI zeigt fehlende Daten als "?" oder leer.
func run(timeout time.Duration, name string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, name, args...)
	var out bytes.Buffer
	cmd.Stdout = &out
	err := cmd.Run()
	return strings.TrimSpace(out.String()), err
}

// === System-Infos ===

type SystemInfo struct {
	PiModel  string
	CPUTemp  string
	CPUBusy  float64 // %; -1 beim Erst-Sample
	CPUIO    float64 // % iowait, separat ausgewiesen
	LoadAvg  string  // "load 7.60" — wartende Prozesse, IO-bound-Indikator
	Uptime   string
	Mem      string
	WlanRSSI string
	DiskRoot string
	DiskDfld string
	DiskBoot string
}

func collectSystem() SystemInfo {
	busy, io := cpuLoadPercent()
	return SystemInfo{
		PiModel:  piModel(),
		CPUTemp:  cpuTemp(),
		CPUBusy:  busy,
		CPUIO:    io,
		LoadAvg:  loadAvg(),
		Uptime:   sysUptime(),
		Mem:      sysMem(),
		WlanRSSI: wlanRSSI(),
		DiskRoot: sysDisk("/"),
		DiskDfld: sysDisk("/opt/dfld"),
		DiskBoot: sysDisk("/boot/firmware"),
	}
}

// loadAvg liest aus /proc/loadavg die 1-min-Load. Ein Wert >> n_cpus
// deutet auf IO-bound oder Scheduler-Druck hin (Prozesse warten in der
// runqueue).
func loadAvg() string {
	b, err := os.ReadFile("/proc/loadavg")
	if err != nil {
		return ""
	}
	f := strings.Fields(string(b))
	if len(f) < 1 {
		return ""
	}
	return f[0]
}

// CPU-Last via /proc/stat Delta zwischen zwei Reads. Beim ersten Aufruf
// gibt's noch keinen Baseline → returnt -1; ab dem zweiten Snapshot kommt
// der echte Wert. iowait wird BEWUSST NICHT zur busy-Zeit gerechnet (was
// die Linux-default-Interpretation waere) — auf Pi Zero 2W mit langsamer
// SD-Karte ist iowait der Hauptanteil und verzerrt sonst die "wie hart
// arbeitet die CPU"-Aussage. busy% und iowait% werden separat geliefert.
var (
	cpuStatMu       sync.Mutex
	cpuLastIdle     uint64
	cpuLastIowait   uint64
	cpuLastTotal    uint64
	cpuHavePrevious bool
)

// cpuLoadPercent gibt (busy%, iowait%) zurueck. busy enthaelt user+system+
// nice+irq+softirq+steal (= Cores arbeiten wirklich); iowait separat
// (= Cores warten auf Disk/Storage). Erstaufruf: (-1, -1).
func cpuLoadPercent() (float64, float64) {
	b, err := os.ReadFile("/proc/stat")
	if err != nil {
		return -1, -1
	}
	// erste Zeile: "cpu  user nice system idle iowait irq softirq steal ..."
	line := strings.SplitN(string(b), "\n", 2)[0]
	f := strings.Fields(line)
	if len(f) < 8 || f[0] != "cpu" {
		return -1, -1
	}
	var total, idle, iowait uint64
	for i := 1; i < 9 && i < len(f); i++ {
		v, _ := strconv.ParseUint(f[i], 10, 64)
		total += v
		switch i {
		case 4:
			idle = v
		case 5:
			iowait = v
		}
	}

	cpuStatMu.Lock()
	defer cpuStatMu.Unlock()
	if !cpuHavePrevious {
		cpuLastIdle = idle
		cpuLastIowait = iowait
		cpuLastTotal = total
		cpuHavePrevious = true
		return -1, -1
	}
	dTotal := total - cpuLastTotal
	dIdle := idle - cpuLastIdle
	dIowait := iowait - cpuLastIowait
	cpuLastIdle = idle
	cpuLastIowait = iowait
	cpuLastTotal = total
	if dTotal == 0 {
		return -1, -1
	}
	busy := 100.0 * float64(dTotal-dIdle-dIowait) / float64(dTotal)
	iowaitPct := 100.0 * float64(dIowait) / float64(dTotal)
	return busy, iowaitPct
}

// Disk-Stats via /proc/diskstats Delta. Wir lesen nur das Aggregat
// "mmcblk0" (= gesamte SD-Karte, summiert ueber alle Partitions) —
// genau das was die SD-Karten-Lebensdauer beeinflusst.
//
// Format /proc/diskstats Felder (0-indexed):
//   [0]=major [1]=minor [2]=name [3..]=stats
//   stats[0]=reads completed  stats[3]=ms reads
//   stats[4]=writes completed stats[6]=sectors written  (1 sector = 512 B)
//   stats[7]=ms writes
var (
	diskStatsMu      sync.Mutex
	diskLastWrites   uint64
	diskLastSectors  uint64
	diskLastTime     time.Time
	diskHavePrevious bool
)

// diskWriteStats returns (writes/s, KB/s) on the boot SD-card device
// (mmcblk0 aggregate). Erstaufruf: (-1, -1).
func diskWriteStats() (opsPerSec float64, kbPerSec float64) {
	b, err := os.ReadFile("/proc/diskstats")
	if err != nil {
		return -1, -1
	}
	var writes, sectors uint64
	found := false
	for _, line := range strings.Split(string(b), "\n") {
		f := strings.Fields(line)
		if len(f) < 11 {
			continue
		}
		name := f[2]
		// Aggregate auf der SD-Karte: "mmcblk0" (ohne pN-Suffix).
		// Partitions wie mmcblk0p1/p2 sind im mmcblk0-Counter implizit
		// summiert; alleinige Auswahl verhindert Doppelzaehlung.
		if name != "mmcblk0" {
			continue
		}
		w, _ := strconv.ParseUint(f[7], 10, 64)
		s, _ := strconv.ParseUint(f[9], 10, 64)
		writes = w
		sectors = s
		found = true
		break
	}
	if !found {
		return -1, -1
	}

	diskStatsMu.Lock()
	defer diskStatsMu.Unlock()
	now := time.Now()
	if !diskHavePrevious {
		diskLastWrites = writes
		diskLastSectors = sectors
		diskLastTime = now
		diskHavePrevious = true
		return -1, -1
	}
	dt := now.Sub(diskLastTime).Seconds()
	if dt <= 0 {
		return -1, -1
	}
	opsPerSec = float64(writes-diskLastWrites) / dt
	// 1 sector = 512 B → KB = sectors * 0.5
	kbPerSec = float64(sectors-diskLastSectors) * 0.5 / dt
	diskLastWrites = writes
	diskLastSectors = sectors
	diskLastTime = now
	return opsPerSec, kbPerSec
}

func piModel() string {
	b, err := os.ReadFile("/proc/device-tree/model")
	if err != nil {
		return "?"
	}
	return strings.TrimRight(string(b), "\x00\n")
}

func cpuTemp() string {
	if out, err := run(time.Second, "vcgencmd", "measure_temp"); err == nil {
		// "temp=57.1'C" → "57.1°C"
		s := strings.TrimPrefix(out, "temp=")
		return strings.ReplaceAll(s, "'", "°")
	}
	if b, err := os.ReadFile("/sys/class/thermal/thermal_zone0/temp"); err == nil {
		if n, err := strconv.Atoi(strings.TrimSpace(string(b))); err == nil {
			return fmt.Sprintf("%.1f°C", float64(n)/1000)
		}
	}
	return "?"
}

func sysUptime() string {
	b, err := os.ReadFile("/proc/uptime")
	if err != nil {
		return "?"
	}
	parts := strings.Fields(string(b))
	if len(parts) == 0 {
		return "?"
	}
	secs, err := strconv.ParseFloat(parts[0], 64)
	if err != nil {
		return "?"
	}
	s := int(secs)
	d := s / 86400
	h := (s % 86400) / 3600
	m := (s % 3600) / 60
	switch {
	case d > 0:
		return fmt.Sprintf("%dd %dh", d, h)
	case h > 0:
		return fmt.Sprintf("%dh %dm", h, m)
	default:
		return fmt.Sprintf("%dm", m)
	}
}

func sysMem() string {
	out, err := run(time.Second, "free", "-h")
	if err != nil {
		return "?"
	}
	for _, line := range strings.Split(out, "\n") {
		if strings.HasPrefix(line, "Mem:") {
			f := strings.Fields(line)
			if len(f) >= 3 {
				return f[2] + "/" + f[1]
			}
		}
	}
	return "?"
}

func wlanRSSI() string {
	b, err := os.ReadFile("/proc/net/wireless")
	if err != nil {
		return ""
	}
	lines := strings.Split(string(b), "\n")
	if len(lines) < 3 {
		return ""
	}
	f := strings.Fields(lines[2])
	if len(f) < 4 {
		return ""
	}
	// Format: "wlan0: 0000 60. -52. -256 0 ..."
	iface := strings.TrimRight(f[0], ":")
	level := strings.TrimRight(f[3], ".")
	result := level + " dBm"
	if ssid := wlanSSID(iface); ssid != "" {
		result += " (" + ssid + ")"
	}
	return result
}

// wlanSSID liest die aktuelle SSID via `iw dev <iface> link`. Falls
// `iw` fehlt oder nichts verbunden: leerer String.
func wlanSSID(iface string) string {
	out, err := run(time.Second, "iw", "dev", iface, "link")
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "SSID:") {
			return strings.TrimSpace(strings.TrimPrefix(line, "SSID:"))
		}
	}
	return ""
}

func sysDisk(path string) string {
	out, err := run(time.Second, "df", "-h", path)
	if err != nil {
		return "?"
	}
	lines := strings.Split(out, "\n")
	if len(lines) < 2 {
		return "?"
	}
	f := strings.Fields(lines[1])
	if len(f) < 5 {
		return "?"
	}
	return fmt.Sprintf("%s/%s (%s)", f[2], f[1], f[4])
}

// === Container ===

type ContainerStatus struct {
	Name         string
	State        string // running|exited|restarting|missing|disabled
	Uptime       string
	RestartCount int
	Disabled     bool
	Flow         string // datafluss-string
	Health       string // ●|⚠|✗|⟳|–|?
}

var (
	InfraContainers = []string{"mqtt", "influxdb", "grafana", "homepage", "telegraf", "ultrafeeder"}
	ConnContainers  = []string{"sensor2mqtt", "mqtt2tsdb", "mqtt2liveview", "mqtt2mqtt", "tsdb2http", "tsdb2ftp", "tsdb2osm", "adsb2mqtt", "detect_flyover"}
)

// ContainerInspect ist das aus `docker inspect` extrahierte Subset.
type ContainerInspect struct {
	Status       string
	StartedAt    string
	RestartCount int
}

// batchInspect ruft `docker inspect <name1> <name2> ...` EINMAL fuer alle
// gefragten Container und parsed das JSON-Array. Fehlende Container landen
// in stderr und werden ignoriert (sie tauchen einfach nicht im result-map
// auf — collectContainer kann dann "missing" anzeigen).
//
// Vorher hatten wir 3 docker-inspect-Calls pro Container (Status, Restart,
// StartedAt) plus parallele goroutines pro Container → bis zu 48 docker-
// daemon-Anfragen parallel, was die Pi-Subprozess-Pool tot machte.
func batchInspect(names []string) map[string]ContainerInspect {
	args := append([]string{"inspect"}, names...)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "docker", args...)
	var stdout bytes.Buffer
	cmd.Stdout = &stdout
	_ = cmd.Run() // missing-Container = exit-1, JSON kommt trotzdem fuer existing
	var results []struct {
		Name  string `json:"Name"`
		State struct {
			Status    string `json:"Status"`
			StartedAt string `json:"StartedAt"`
		} `json:"State"`
		RestartCount int `json:"RestartCount"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &results); err != nil {
		return nil
	}
	m := make(map[string]ContainerInspect, len(results))
	for _, r := range results {
		name := strings.TrimPrefix(r.Name, "/")
		m[name] = ContainerInspect{
			Status:       r.State.Status,
			StartedAt:    r.State.StartedAt,
			RestartCount: r.RestartCount,
		}
	}
	return m
}

// inspectUptime formatiert StartedAt-RFC3339-String in "5m"/"3h"/"2d".
func inspectUptime(startedAt string) string {
	if startedAt == "" || strings.HasPrefix(startedAt, "0001") {
		return "—"
	}
	t, err := time.Parse(time.RFC3339Nano, startedAt)
	if err != nil {
		return "—"
	}
	return humanDur(time.Since(t))
}

func humanDur(d time.Duration) string {
	s := int(d.Seconds())
	switch {
	case s < 60:
		return fmt.Sprintf("%ds", s)
	case s < 3600:
		return fmt.Sprintf("%dm", s/60)
	case s < 86400:
		return fmt.Sprintf("%dh", s/3600)
	default:
		return fmt.Sprintf("%dd", s/86400)
	}
}

// disabledReason gibt einen kompakten Hinweis, WARUM ein Container
// disabled ist — yaml-Config (osm/bridge leer), Hardware-Erkennung
// (kein ADS-B / kein USB-Stick), oder mini-Mode (grafana/homepage/
// telegraf gehoeren beim mini-Deploy nicht ins Compose).
func disabledReason(name string, cfg DfldConfig) string {
	switch name {
	case "tsdb2osm":
		if cfg.OsmAPIKey == "" {
			return "(osm_api_key leer)"
		}
	case "mqtt2mqtt":
		if cfg.MqttBroker == "" {
			return "(mqtt_bridged_broker leer)"
		}
	case "adsb2mqtt", "detect_flyover", "ultrafeeder":
		return "(kein ADS-B-USB-Stick erkannt)"
	case "grafana", "homepage", "telegraf":
		return "(nicht im mini-Deploy)"
	}
	return "(nicht im Compose)"
}

// isDisabled leitet aus dfld.yml und Compose-Files ab, ob ein Container
// per Konfiguration nicht laufen soll (also "–" statt "✗" anzuzeigen ist).
//
// Zwei-stufige Logik:
//   1. dfld.yml-basierte Deaktivierung (osm leer, mqtt-bridge leer)
//   2. Generischer Compose-File-Check: existiert der Container ueberhaupt
//      in den deployten Compose-Files? Falls nicht (z.B. Mini-Mode ohne
//      grafana/homepage/telegraf/ultrafeeder/adsb2mqtt/detect_flyover),
//      ist "disabled" die ehrlichere Anzeige als "✗ down".
func isDisabled(name string, cfg DfldConfig) bool {
	switch name {
	case "tsdb2osm":
		if cfg.OsmAPIKey == "" {
			return true
		}
	case "mqtt2mqtt":
		if cfg.MqttBroker == "" {
			return true
		}
	}
	// Generischer Check fuer alle Container: in einem der deployten
	// Compose-Files referenziert?
	for _, p := range []string{
		pathConnectorsDir + "/docker-compose.yml",
		"/opt/dfld/infrastructure/docker-compose.yml",
	} {
		b, err := os.ReadFile(p)
		if err == nil && bytes.Contains(b, []byte("container_name: "+name)) {
			return false
		}
	}
	return true
}

func collectContainer(name string, cfg DfldConfig, inspect map[string]ContainerInspect) ContainerStatus {
	s := ContainerStatus{Name: name}
	if isDisabled(name, cfg) {
		s.State = "disabled"
		s.Disabled = true
		s.Health = "–"
		s.Flow = disabledReason(name, cfg)
		return s
	}
	if ins, ok := inspect[name]; ok {
		s.State = ins.Status
		s.RestartCount = ins.RestartCount
		s.Uptime = inspectUptime(ins.StartedAt)
	} else {
		s.State = "missing"
	}
	s.Flow = flowFor(name)
	s.Health = healthGlyph(s)
	return s
}

func healthGlyph(s ContainerStatus) string {
	switch s.State {
	case "running":
		if s.RestartCount > 5 || strings.Contains(s.Flow, "stale") || strings.Contains(s.Flow, "0/1m") {
			return "⚠"
		}
		return "●"
	case "exited", "dead":
		return "✗"
	case "restarting":
		return "⟳"
	case "missing":
		return "?"
	}
	return "?"
}

// === Datenfluss pro Container ===

func flowFor(name string) string {
	switch name {
	case "mqtt":
		r := brokerPublishPerMin()
		if r < 0 {
			return "rate: stale"
		}
		return fmt.Sprintf("publish: %.0f msg/min", r)
	case "influxdb":
		return "ping: " + httpCode("http://127.0.0.1:8086/ping")
	case "grafana":
		return "health: " + httpCode("http://127.0.0.1:3000/api/health")
	case "homepage":
		return "HTTP: " + httpCode("http://127.0.0.1")
	case "telegraf":
		return fmt.Sprintf("writes/5m: %d", logCount("telegraf", `Wrote.*metrics`, "5m"))
	case "ultrafeeder":
		n := aircraftCount()
		if n < 0 {
			return "aircraft.json: fail"
		}
		return fmt.Sprintf("aircraft: %d sichtbar", n)
	case "sensor2mqtt":
		// Live-Count aus dem permanent laufenden MQTT-Stream (kein neuer
		// Subprozess pro Snapshot). InfluxDB-Count parallel ueber HTTP.
		liveCount := topicCounter.CountWindow("dfld/sensors/noise/spl", 5*time.Second)
		tsdbCount := influxCount("spl", time.Hour)
		tsdbPart := "?"
		if tsdbCount >= 0 {
			tsdbPart = fmt.Sprintf("%d", tsdbCount)
		}
		return fmt.Sprintf("spl: %d msgs/5s (live)  %s msgs/1h (tsdb)", liveCount, tsdbPart)
	case "mqtt2tsdb":
		// Hinweis: dieser Wert wird auch in der Uebersicht-Datenfluss-Box
		// als snap.Spl1m angezeigt. Wir koennten ihn dort cachen, aber
		// dann muesste flowFor das snap kennen — refactor for later.
		// Mit dem influxSemaphore (max 2 parallel) ist die Duplikation
		// nicht mehr Pi-fatal.
		n := influxCount("spl", time.Minute)
		if n < 0 {
			return "spl/1m: — (probe error)"
		}
		return fmt.Sprintf("spl/1m: %d (tsdb)", n)
	case "mqtt2liveview":
		// Input-Rate aus MQTT-Stream-Cache + 10-Min-Stats aus Container-Log.
		inCount := topicCounter.CountWindow("dfld/sensors/noise/spl/#", 5*time.Second)
		s := parseStatsLine("mqtt2liveview", "sent")
		if !s.OK {
			if s.Primary == -1 {
				return fmt.Sprintf("in: %d msgs/5s  (Stats probe timeout)", inCount)
			}
			return fmt.Sprintf("in: %d msgs/5s  (warte auf 1. Stats-Log)", inCount)
		}
		conn := "✓"
		if !s.Connected {
			conn = "✗"
		}
		ratePart := ""
		if s.RatePerSec >= 0 {
			ratePart = fmt.Sprintf("  rate=%.1f/s", s.RatePerSec)
		}
		return fmt.Sprintf("sent=%d  drop=%d  mqtt=%s%s", s.Primary, s.Dropped, conn, ratePart)
	case "mqtt2mqtt":
		// Parse die periodische Stats-Zeile (alle 10min) aus dem Log:
		//   "Stats: forwarded=N, dropped=N, connected=True"
		// Zuverlaessig + Rate aus Delta zwischen letzten zwei Stats.
		s := parseStatsLine("mqtt2mqtt", "forwarded")
		if !s.OK {
			if s.Primary == -1 {
				return "(probe timeout)"
			}
			return "keine Stats (warte auf 1. Logging)"
		}
		conn := "✓"
		if !s.Connected {
			conn = "✗"
		}
		ratePart := ""
		if s.RatePerSec >= 0 {
			ratePart = fmt.Sprintf("  rate=%.1f/s", s.RatePerSec)
		}
		return fmt.Sprintf("fwd=%d  drop=%d  conn=%s%s", s.Primary, s.Dropped, conn, ratePart)
	case "tsdb2http":
		// Eigenes State-File (mtime = last successful HTTPS-batch).
		st, err := os.Stat(pathTsdb2httpState)
		if err != nil {
			return "kein State-File"
		}
		return "last-tx " + humanDur(time.Since(st.ModTime())) + " ago"
	case "tsdb2ftp":
		// Erfolgs-Marker im Log: "transfering NNNNN bytes ... via ftp"
		// (eine pro stuendlichem Hochlade-Zyklus).
		if t, ok := lastLogMatch("tsdb2ftp", "transfering.*via ftp"); ok {
			return "last-tx " + humanDur(time.Since(t)) + " ago"
		}
		return "last-tx —"
	case "tsdb2osm":
		// Erfolgs-Marker im Log: "Successfully sent value X.XX to sensor ..."
		// (eine pro Upload, alle ~2-3 Minuten).
		if t, ok := lastLogMatch("tsdb2osm", "Successfully sent value"); ok {
			return "last-tx " + humanDur(time.Since(t)) + " ago"
		}
		return "last-tx —"
	case "adsb2mqtt":
		// Count aus MQTT-Stream-Cache, kein extra Subprozess.
		n := topicCounter.CountWindow("dfld/adsb/#", 5*time.Second)
		return fmt.Sprintf("adsb: %d msgs/5s (live)", n)
	case "detect_flyover":
		n := influxCount("flyover", time.Hour)
		if n < 0 {
			return "events/h: — (probe error)"
		}
		return fmt.Sprintf("events/h: %d", n)
	}
	return "—"
}

// === Probes ===

// mqttCount captured `sample` Sekunden lang auf einem Topic und gibt
// die rohe Anzahl Messages zurueck. Bei Fehler -1.
//
// Implementiert via `timeout N mosquitto_sub ... | wc -l` ueber `sh -c`,
// weil mosquitto_sub kein eigenes Sample-Duration-Flag hat (-W ist nur
// "exit if no message in N seconds").
func mqttCount(topic string, sample time.Duration) int {
	sec := int(sample.Seconds())
	if sec < 1 {
		sec = 1
	}
	out, err := run(sample+2*time.Second, "sh", "-c",
		fmt.Sprintf("timeout %d mosquitto_sub -h 127.0.0.1 -p 1883 -t %q 2>/dev/null | wc -l",
			sec, topic))
	if err != nil {
		return -1
	}
	n, err := strconv.Atoi(strings.TrimSpace(out))
	if err != nil {
		return -1
	}
	return n
}

// brokerPublishPerMin liest "$SYS/broker/load/publish/sent/1min" — moving-average
// publish-rate ueber 1min (msg/min), vom Broker selbst gepflegt. Sehr ruhig
// im Vergleich zu eigenem Sampling.
func brokerPublishPerMin() float64 {
	out, err := run(3*time.Second, "mosquitto_sub", "-h", "127.0.0.1",
		"-t", "$SYS/broker/load/publish/sent/1min", "-C", "1", "-W", "2")
	if err != nil || out == "" {
		return -1
	}
	v, err := strconv.ParseFloat(strings.TrimSpace(out), 64)
	if err != nil {
		return -1
	}
	return v
}

// === Stats-Line Parser (generisch) ===
//
// Format aus den Python-Containern (alle 10min auf stderr via logging):
//   mqtt2mqtt:    "2026-05-13 21:51:48,805 - INFO:Stats: forwarded=N, dropped=N, connected=True"
//   mqtt2liveview: "2026-05-13 21:51:48,805 - INFO:Stats: sent=N, dropped=N, connected=True"
//
// Wir lesen die letzten zwei Stats-Zeilen und leiten — wenn moeglich —
// die Rate aus dem Delta zwischen ihnen ab (zuverlaessig, weil die
// Stats-Lines selbst-zeitgestempelt sind via Python-Logging-Format).

type StatsLine struct {
	Primary    int     // Primaerzaehler (forwarded / sent)
	Dropped    int
	Connected  bool
	RatePerSec float64 // delta(Primary) / delta(ts) zwischen letzten beiden Stats-Zeilen
	OK         bool
}

var statsTsRe = regexp.MustCompile(`^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})`)

// parseStatsLine zieht die letzten Stats-Zeilen aus dem Container-Log
// und parst sie. `primaryKey` ist der Name des Hauptzaehlers in der
// Log-Zeile (z.B. "forwarded" oder "sent").
// StatsErrTimeout signalisiert dem flow-formatter dass docker logs in der
// Probe-Zeit nicht antwortete (Pi-Schwergang) — anders als "Stats fehlen
// noch", was erst nach Container-Restart fuer ~10 Min normal ist.
const StatsErrTimeout = "__timeout__"

func parseStatsLine(container, primaryKey string) StatsLine {
	res := StatsLine{RatePerSec: -1}
	// 8s statt 3s: bei voller Pi-Last (compose recreate, telegraf-burst)
	// dauert ein "docker logs --tail 400" auch mal mehrere Sekunden.
	out, err := run(8*time.Second, "sh", "-c",
		fmt.Sprintf("docker logs --tail 400 %s 2>&1 | grep 'Stats:' | tail -2", container))
	if err != nil {
		// docker logs hat einen anderen Fehler als "leeres Output" —
		// signalisieren ueber spezielles Field damit der Renderer
		// "(probe timeout)" anzeigt statt "noch nicht geloggt".
		res.Primary = -1
		return res
	}
	if out == "" {
		return res // OK=false, ohne Timeout-Marker → "noch nicht geloggt"
	}
	lineRe := regexp.MustCompile(primaryKey + `=(\d+).*?dropped=(\d+).*?connected=(\w+)`)
	lines := strings.Split(strings.TrimSpace(out), "\n")
	last := lines[len(lines)-1]
	m := lineRe.FindStringSubmatch(last)
	if m == nil {
		return res
	}
	res.Primary, _ = strconv.Atoi(m[1])
	res.Dropped, _ = strconv.Atoi(m[2])
	res.Connected = m[3] == "True"
	res.OK = true

	if len(lines) >= 2 {
		prev := lines[len(lines)-2]
		mp := lineRe.FindStringSubmatch(prev)
		tp := statsTsRe.FindStringSubmatch(prev)
		tl := statsTsRe.FindStringSubmatch(last)
		if mp != nil && tp != nil && tl != nil {
			primPrev, _ := strconv.Atoi(mp[1])
			t0, e0 := time.Parse("2006-01-02 15:04:05", tp[1])
			t1, e1 := time.Parse("2006-01-02 15:04:05", tl[1])
			if e0 == nil && e1 == nil && t1.After(t0) {
				dt := t1.Sub(t0).Seconds()
				if dt > 0 {
					res.RatePerSec = float64(res.Primary-primPrev) / dt
				}
			}
		}
	}
	return res
}

// aircraftCount liest /data/aircraft.json vom ultrafeeder und zaehlt
// die "aircraft"-Array-Laenge — entspricht "wie viele Flieger sieht
// die Pi gerade". Erklaert i.d.R. warum adsb-Rate niedrig ist.
func aircraftCount() int {
	out, err := run(3*time.Second, "curl", "-s", "--max-time", "2",
		"http://127.0.0.1:8080/data/aircraft.json")
	if err != nil || out == "" {
		return -1
	}
	var parsed struct {
		Aircraft []map[string]any `json:"aircraft"`
	}
	if err := json.Unmarshal([]byte(out), &parsed); err != nil {
		return -1
	}
	return len(parsed.Aircraft)
}

func httpCode(url string) string {
	// max-time 5s + run-context 6s: bei busy Pi war 2s zu knapp und
	// gab false "ping fail" obwohl influxdb antwortet (curl-call selbst
	// dauert auf der Pi ~0.5-1s, plus moegliche TCP-Connect-Latenz).
	out, err := run(6*time.Second, "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "5", url)
	if err != nil {
		return "fail"
	}
	return out
}

// influxHTTPClient mit kurz-lebigem keep-alive — pro Snapshot werden
// einige Queries gleichzeitig gemacht, die HTTP-API ist parallel-faehig
// (anders als `docker exec influxdb influx`, das je Aufruf ~500ms CLI-
// Startup-Overhead kostet plus single-CLI-bottleneck).
var influxHTTPClient = &http.Client{
	Timeout: 15 * time.Second,
}

// influxCount fuehrt SELECT count(*) FROM <measurement> WHERE time > now()-<window>.
//
// Wir addieren intern +5ms zum Window. Zwei Anti-Glitch-Effekte:
//
//   1. Boundary-vs-Sample-Tick: InfluxDB-WHERE ist strikt ">", ohne
//      Polster faellt der aelteste-erwartete Sample mal rein, mal raus.
//   2. Kumulative Drift bei langen Windows: die DNMS-Sample-Periode
//      liegt nicht exakt bei 1.000s sondern bei ca. 1.000073s (gemessen
//      ueber 120 Samples). Ueber 1h sind das +263ms kumulative Drift,
//      d.h. ohne Polster sieht man regelmaessig 3599 statt 3600.
//
// Trade-off der Groesse: +1ms reicht fuer den Einzel-Jitter (<0.5ms),
// aber nicht fuer die kumulierte Drift. +5ms deckt beides komfortabel
// ab; das Risiko fuer ein zusaetzliches 61 ueber das Minuten-Fenster
// ist 0.5% (5ms/1s), praktisch unsichtbar.
//
// WICHTIG: InfluxQL akzeptiert keine Float-Sekunden ("60.005s" → parse error).
// Daher verwenden wir Millisekunden-Suffix mit Integer-Wert ("60005ms").
// influxResponse spiegelt das InfluxDB-v1 /query-JSON-Format wider:
//   {"results":[{"series":[{"name":"spl","columns":[...],"values":[[ts,c1,c2,...]]}]}]}
type influxResponse struct {
	Results []struct {
		Series []struct {
			Name    string          `json:"name"`
			Columns []string        `json:"columns"`
			Values  [][]interface{} `json:"values"`
		} `json:"series"`
	} `json:"results"`
}

func influxCount(measurement string, window time.Duration) int {
	ms := window.Milliseconds() + 5
	q := fmt.Sprintf("SELECT count(*) FROM %s WHERE time > now()-%dms", measurement, ms)
	u := fmt.Sprintf("http://127.0.0.1:8086/query?db=dfld&q=%s", url.QueryEscape(q))

	resp, err := influxHTTPClient.Get(u)
	if err != nil {
		return -1
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return -1
	}

	var r influxResponse
	if err := json.NewDecoder(resp.Body).Decode(&r); err != nil {
		return -1
	}
	if len(r.Results) == 0 || len(r.Results[0].Series) == 0 || len(r.Results[0].Series[0].Values) == 0 {
		return 0
	}
	row := r.Results[0].Series[0].Values[0]
	// Erstes Element ist time, ab Index 1 die count_*-Werte. Wir nehmen
	// den ersten count — bei SELECT count(*) FROM spl sind das die
	// Counts der Felder (alle gleich, weil 1:1-Field-Mapping pro Sample).
	if len(row) < 2 {
		return 0
	}
	if v, ok := row[1].(float64); ok {
		return int(v)
	}
	return 0
}

func logCount(container, pattern, since string) int {
	out, err := run(5*time.Second, "sh", "-c",
		fmt.Sprintf("docker logs --since %s %s 2>&1 | grep -cE %q", since, container, pattern))
	if err != nil && out == "" {
		return 0
	}
	n, _ := strconv.Atoi(out)
	return n
}

// === Aggregate Snapshot für die UI ===

type Snapshot struct {
	When         time.Time
	Cfg          DfldConfig
	Freeze       bool
	System       SystemInfo
	Infra        []ContainerStatus
	Conn         []ContainerStatus
	ConfOK       bool
	Spl1m        int     // count(spl) aus InfluxDB last 1 min — bei 1Hz erwartet 60
	BrokerPerMin float64 // msg/min broker-weit (alle Topics) ueber $SYS
	DiskOps      float64 // SD-Karten-Schreibops/s (mmcblk0)
	DiskKB       float64 // SD-Karten-Schreibbytes in KB/s
	Spl5m        int     // count(spl) last 5 min — fuer Trend
	FlyH         int
	Aircraft     int
	TxAge        string
}

// collectSnapshot fuehrt ALLE Daten-Probes parallel aus. Ohne goroutines
// wuerden alleine die mqttRate-Calls (2s blockend) und log/influx-Queries
// die Refresh-Zeit auf >30s drueken. Mit WaitGroup landet alles in ~3s.
func collectSnapshot() Snapshot {
	cfg := loadConfig()
	snap := Snapshot{
		When:   time.Now(),
		Cfg:    cfg,
		Freeze: freezeActive(),
		System: collectSystem(),
		// ConfOK wird in einer der parallelen Goroutines weiter unten
		// gesetzt — validateConfig() ist heute ~16s langsam (dfld-config.sh
		// yq-Schleife), darf den Critical-Path nicht blockieren.
	}
	snap.Infra = make([]ContainerStatus, len(InfraContainers))
	snap.Conn = make([]ContainerStatus, len(ConnContainers))

	// Alle docker-inspect-Anfragen in EINEM Call buendeln — statt
	// 16×3 = 48 parallelen docker-daemon-Subprozessen nur einer.
	all := append([]string{}, InfraContainers...)
	all = append(all, ConnContainers...)
	inspect := batchInspect(all)

	var wg sync.WaitGroup

	// Container-flow-Probes parallel (docker logs grep, MQTT/influx
	// samples). State/Uptime/Restart kommen aus dem batchInspect oben.
	for i, name := range InfraContainers {
		wg.Add(1)
		go func(i int, n string) {
			defer wg.Done()
			snap.Infra[i] = collectContainer(n, cfg, inspect)
		}(i, name)
	}
	for i, name := range ConnContainers {
		wg.Add(1)
		go func(i int, n string) {
			defer wg.Done()
			snap.Conn[i] = collectContainer(n, cfg, inspect)
		}(i, name)
	}

	// Overview-spezifische Probes parallel (jede goroutine schreibt
	// auf ein eigenes Feld — kein Race).
	wg.Add(6)
	go func() { defer wg.Done(); snap.Spl1m = influxCount("spl", time.Minute) }()
	go func() { defer wg.Done(); snap.BrokerPerMin = brokerPublishPerMin() }()
	go func() { defer wg.Done(); snap.Spl5m = influxCount("spl", 5*time.Minute) }()
	go func() { defer wg.Done(); snap.FlyH = influxCount("flyover", time.Hour) }()
	go func() { defer wg.Done(); snap.ConfOK = validateConfig() }()
	go func() { defer wg.Done(); snap.DiskOps, snap.DiskKB = diskWriteStats() }()

	wg.Wait()

	// Ableitungen aus parallelen Daten + sync-only:
	snap.Aircraft = -1
	for _, c := range snap.Infra {
		if c.Name == "ultrafeeder" && !c.Disabled {
			// Aircraft-Count steht schon im flow-String der ultrafeeder-Zeile;
			// wir holen ihn nochmal sauber fuer die Overview-Box.
			snap.Aircraft = aircraftCount()
			break
		}
	}
	if st, err := os.Stat(pathTsdb2httpState); err == nil {
		snap.TxAge = humanDur(time.Since(st.ModTime())) + " ago"
	} else {
		snap.TxAge = "—"
	}
	return snap
}

func validateConfig() bool {
	if _, err := exec.LookPath("dfld-config"); err != nil {
		return false
	}
	// dfld-config.sh ist heute ~16s langsam (13 sequentielle yq-Aufrufe).
	// 30s Timeout damit der Snapshot bei Erst-Run nicht in einer Endlos-
	// Wartezeit haengt. Eigentliche Fix gehoert in dfld-config.sh (yq
	// konsolidieren), aber bis dahin: lieber Timeout als haengen.
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	err := exec.CommandContext(ctx, "dfld-config", "validate").Run()
	return err == nil
}

// === MQTT live stream (für SPL-Chart + Sensoren-Liste) ===

// MqttMsg ist eine empfangene Live-Message vom lokalen Broker.
type MqttMsg struct {
	Topic   string
	Payload string
	Time    time.Time
}

// MqttStream wrappt einen lang laufenden `mosquitto_sub`-Subprozess und
// liefert empfangene Messages auf Msgs aus. Stop()-Aufruf killt den
// Subprozess und schliesst Msgs.
type MqttStream struct {
	cmd  *exec.Cmd
	Msgs chan MqttMsg
	stop chan struct{}
}

// StartMqttStream subscribed `dfld/#` und gibt einen Channel mit allen
// einlaufenden Messages zurueck. Das `-v`-Flag bei mosquitto_sub setzt
// das Format auf "<topic> <payload>" pro Zeile.
func StartMqttStream() (*MqttStream, error) {
	cmd := exec.Command("mosquitto_sub",
		"-h", "127.0.0.1", "-p", "1883",
		"-t", "dfld/#", "-v", "-q", "0")
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	s := &MqttStream{
		cmd:  cmd,
		Msgs: make(chan MqttMsg, 512),
		stop: make(chan struct{}),
	}
	go func() {
		defer close(s.Msgs)
		scanner := bufio.NewScanner(stdout)
		scanner.Buffer(make([]byte, 65536), 1024*1024)
		for scanner.Scan() {
			line := scanner.Text()
			sep := strings.Index(line, " ")
			if sep < 0 {
				continue
			}
			msg := MqttMsg{
				Topic:   line[:sep],
				Payload: line[sep+1:],
				Time:    time.Now(),
			}
			select {
			case s.Msgs <- msg:
			case <-s.stop:
				return
			}
		}
	}()
	return s, nil
}

func (s *MqttStream) Stop() {
	if s == nil {
		return
	}
	select {
	case <-s.stop:
		// schon geschlossen
	default:
		close(s.stop)
	}
	if s.cmd != nil && s.cmd.Process != nil {
		_ = s.cmd.Process.Kill()
		_ = s.cmd.Wait()
	}
}

// === SPL Ring Buffer ===

// SplRing speichert die letzten N dB(A)-Samples in Empfangs-Reihenfolge.
type SplRing struct {
	values []float64
	cap    int
}

func NewSplRing(capacity int) *SplRing {
	if capacity < 1 {
		capacity = 1
	}
	return &SplRing{cap: capacity}
}

func (r *SplRing) Push(v float64) {
	r.values = append(r.values, v)
	if len(r.values) > r.cap {
		r.values = r.values[len(r.values)-r.cap:]
	}
}

// Resize aendert die Kapazitaet; behaelt die neuesten Werte.
func (r *SplRing) Resize(capacity int) {
	if capacity < 1 {
		capacity = 1
	}
	r.cap = capacity
	if len(r.values) > capacity {
		r.values = r.values[len(r.values)-capacity:]
	}
}

func (r *SplRing) Values() []float64 { return r.values }
func (r *SplRing) Len() int          { return len(r.values) }

func (r *SplRing) Last() float64 {
	if len(r.values) == 0 {
		return 0
	}
	return r.values[len(r.values)-1]
}

func (r *SplRing) Stats() (min, max, avg float64) {
	if len(r.values) == 0 {
		return 0, 0, 0
	}
	min, max = r.values[0], r.values[0]
	var sum float64
	for _, v := range r.values {
		if v < min {
			min = v
		}
		if v > max {
			max = v
		}
		sum += v
	}
	avg = sum / float64(len(r.values))
	return
}

// === Sensoren-Liste (Topic-State-Map) ===

type SensorEntry struct {
	Payload string
	Time    time.Time
}

// TopicCounter zaehlt eingehende MQTT-Messages pro Topic in einem
// sliding 60s-Fenster. Wird vom MqttStream-Receiver (main.go Update)
// gefuettert und von flowFor() abgefragt — vermeidet kostspielige
// mosquitto_sub-Subprozesse pro Snapshot.
type TopicCounter struct {
	mu      sync.Mutex
	samples map[string][]time.Time
}

// topicCounter ist die Singleton-Instanz die der Stream beschreibt.
var topicCounter = &TopicCounter{samples: map[string][]time.Time{}}

func (tc *TopicCounter) Record(topic string, t time.Time) {
	tc.mu.Lock()
	defer tc.mu.Unlock()
	tc.samples[topic] = append(tc.samples[topic], t)
	cutoff := t.Add(-60 * time.Second)
	arr := tc.samples[topic]
	i := 0
	for ; i < len(arr) && arr[i].Before(cutoff); i++ {
	}
	if i > 0 {
		tc.samples[topic] = arr[i:]
	}
}

// CountWindow zaehlt alle Messages in `window` deren Topic auf `pattern`
// matched (MQTT-Wildcards + und # unterstuetzt).
func (tc *TopicCounter) CountWindow(pattern string, window time.Duration) int {
	tc.mu.Lock()
	defer tc.mu.Unlock()
	cutoff := time.Now().Add(-window)
	count := 0
	for topic, times := range tc.samples {
		if !topicMatch(topic, pattern) {
			continue
		}
		for _, t := range times {
			if t.After(cutoff) {
				count++
			}
		}
	}
	return count
}

// topicMatch implementiert MQTT-wildcard matching: + = ein Level, # = rest.
func topicMatch(topic, pattern string) bool {
	if pattern == topic {
		return true
	}
	tparts := strings.Split(topic, "/")
	pparts := strings.Split(pattern, "/")
	for i, pp := range pparts {
		if pp == "#" {
			return true
		}
		if i >= len(tparts) {
			return false
		}
		if pp == "+" || pp == tparts[i] {
			continue
		}
		return false
	}
	return len(tparts) == len(pparts)
}

// SensorState mappt Topic → letzte Message.
type SensorState struct {
	mu    sync.Mutex
	state map[string]SensorEntry
}

func NewSensorState() *SensorState {
	return &SensorState{state: map[string]SensorEntry{}}
}

func (s *SensorState) Update(topic, payload string, ts time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.state[topic] = SensorEntry{Payload: payload, Time: ts}
}

// Snapshot gibt eine alphabetisch sortierte Liste aller Topics zurueck.
type SensorRow struct {
	Topic   string
	Payload string
	Age     time.Duration
}

func (s *SensorState) Snapshot() []SensorRow {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now()
	topics := make([]string, 0, len(s.state))
	for t := range s.state {
		topics = append(topics, t)
	}
	sort.Strings(topics)
	rows := make([]SensorRow, 0, len(topics))
	for _, t := range topics {
		e := s.state[t]
		rows = append(rows, SensorRow{
			Topic:   t,
			Payload: e.Payload,
			Age:     now.Sub(e.Time),
		})
	}
	return rows
}

// PrettyPayload formatiert MQTT-Payloads kompakt fuer die Liste-Anzeige.
// JSON-Objekte → "k1=v1 k2=v2 …" (max 3 Keys, sortiert), Skalare direkt,
// alles auf maxLen Zeichen gestutzt.
func PrettyPayload(payload string, maxLen int) string {
	p := strings.TrimSpace(payload)
	if p == "" {
		return ""
	}
	var obj interface{}
	if err := json.Unmarshal([]byte(p), &obj); err == nil {
		switch v := obj.(type) {
		case map[string]interface{}:
			keys := make([]string, 0, len(v))
			for k := range v {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			if len(keys) > 3 {
				keys = keys[:3]
			}
			parts := make([]string, 0, len(keys))
			for _, k := range keys {
				parts = append(parts, fmt.Sprintf("%s=%v", k, v[k]))
			}
			return truncate(strings.Join(parts, " "), maxLen)
		default:
			return truncate(fmt.Sprintf("%v", v), maxLen)
		}
	}
	return truncate(p, maxLen)
}

func truncate(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	if maxLen <= 3 {
		return s[:maxLen]
	}
	return s[:maxLen-1] + "…"
}

// containerLogs holt die letzten N Zeilen Container-Logs mit Timestamps.
// WICHTIG: 2>&1 inkludieren — die Python-Container loggen via stdlib-logging
// auf STDERR (Python-Default). Ohne Redirect bekommen wir einen leeren Body.
func containerLogs(name string, tail int) string {
	out, err := run(10*time.Second, "sh", "-c",
		fmt.Sprintf("docker logs --tail %d --timestamps %s 2>&1", tail, name))
	if err != nil && out == "" {
		return fmt.Sprintf("(Fehler beim Abruf: %v)", err)
	}
	return out
}

// lastLogMatch findet die letzte Log-Zeile, die `pattern` (egrep)
// matched, und gibt deren Docker-Timestamp zurueck. Wird fuer
// "last-tx X ago"-Anzeigen bei tsdb2ftp/tsdb2osm/mqtt2liveview genutzt
// (die nicht — wie tsdb2http — ein eigenes State-File pflegen).
// 10s timeout: bei busy Pi koennen docker logs Aufrufe haengen.
func lastLogMatch(container, pattern string) (time.Time, bool) {
	out, err := run(10*time.Second, "sh", "-c",
		fmt.Sprintf("docker logs --tail 500 --timestamps %s 2>&1 | grep -E %q | tail -1",
			container, pattern))
	if err != nil || out == "" {
		return time.Time{}, false
	}
	// Docker-Timestamp ist erstes Feld: "2026-05-13T22:00:53.543938289Z ..."
	fields := strings.SplitN(strings.TrimSpace(out), " ", 2)
	if len(fields) == 0 {
		return time.Time{}, false
	}
	t, err := time.Parse(time.RFC3339Nano, fields[0])
	if err != nil {
		return time.Time{}, false
	}
	return t, true
}
