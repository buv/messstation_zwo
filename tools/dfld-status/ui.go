// Styling + Render-Funktionen mit lipgloss.

package main

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"
)

// === Farb-Paletten ===
var (
	colBorder   = lipgloss.Color("63")  // helles Blau
	colTitle    = lipgloss.Color("87")  // cyan
	colMuted    = lipgloss.Color("245") // grau
	colGood     = lipgloss.Color("82")  // grün
	colWarn     = lipgloss.Color("214") // orange
	colBad      = lipgloss.Color("196") // rot
	colDisabled = lipgloss.Color("240") // dunkelgrau
	colFreeze   = lipgloss.Color("196") // rot
	colLabel    = lipgloss.Color("117") // hellblau
)

var (
	stTitle = lipgloss.NewStyle().Foreground(colTitle).Bold(true)
	stMuted = lipgloss.NewStyle().Foreground(colMuted)
	stLabel = lipgloss.NewStyle().Foreground(colLabel)

	stBox = lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(colBorder).
		Padding(0, 1)

	stStatusFreeze = lipgloss.NewStyle().Foreground(colFreeze).Bold(true).Blink(true)
	stStatusOK     = lipgloss.NewStyle().Foreground(colGood)
)

func glyphStyle(g string) lipgloss.Style {
	switch g {
	case "●":
		return lipgloss.NewStyle().Foreground(colGood).Bold(true)
	case "⚠":
		return lipgloss.NewStyle().Foreground(colWarn).Bold(true)
	case "✗":
		return lipgloss.NewStyle().Foreground(colBad).Bold(true)
	case "⟳":
		return lipgloss.NewStyle().Foreground(colWarn)
	case "–":
		return lipgloss.NewStyle().Foreground(colDisabled)
	}
	return lipgloss.NewStyle().Foreground(colMuted)
}

// === Render: Overview ===

func renderOverview(s Snapshot, w int) string {
	if w < 60 {
		w = 60
	}

	header := renderHeader(s, w)

	// Drei Boxen nebeneinander: System | Konfiguration | Container-Summary
	hardware := boxSystem(s)
	konfig := boxKonfig(s)
	summary := boxSummary(s)

	row1 := lipgloss.JoinHorizontal(lipgloss.Top, hardware, konfig, summary)

	disk := boxDisk(s, lipgloss.Width(row1))
	flow := boxFlow(s, lipgloss.Width(row1))

	footer := stMuted.Render(" [r] Aktualisieren   [2] Container-Detail   [3] Logs   [4] SPL live   [5] Sensoren   [q] Beenden")

	return lipgloss.JoinVertical(lipgloss.Left,
		header,
		row1,
		disk,
		flow,
		"",
		footer,
	)
}

func renderHeader(s Snapshot, w int) string {
	sid := s.Cfg.StationID()
	city := s.Cfg.StationCity
	if city == "" {
		city = "—"
	}
	left := stTitle.Render(fmt.Sprintf(" dfld-status  ▸  %s  (%s)", sid, city))

	var status string
	if s.Freeze {
		status = stStatusFreeze.Render("FREEZE AKTIV — Connectors gestoppt")
	} else {
		status = stStatusOK.Render("Connectors aktiv")
	}
	right := stMuted.Render(s.When.Format("15:04:05"))

	gap := strings.Repeat(" ", maxInt(1, w-lipgloss.Width(left)-lipgloss.Width(status)-lipgloss.Width(right)-2))
	return lipgloss.JoinHorizontal(lipgloss.Top, left, "  ", status, gap, right)
}

func boxSystem(s Snapshot) string {
	body := renderKVBlock([]kvItem{
		{"Modell", trim(s.System.PiModel, 22)},
		{"CPU", s.System.CPUTemp},
		{"Uptime", s.System.Uptime},
		{"RAM", s.System.Mem},
		{"WLAN", coalesce(s.System.WlanRSSI, "—")},
	})
	return stBox.Width(34).Render(titleLine("System") + "\n" + body)
}

func boxKonfig(s Snapshot) string {
	conf := stStatusOK.Render("valid ✓")
	if !s.ConfOK {
		conf = lipgloss.NewStyle().Foreground(colBad).Render("INVALID ✗")
	}

	live := "—"
	if s.Cfg.LiveEnabled != nil {
		if *s.Cfg.LiveEnabled {
			live = "an"
		} else {
			live = "aus"
		}
	}

	bf := coalesce(s.Cfg.Backfill, "—")
	osm := "aus"
	if s.Cfg.OsmAPIKey != "" {
		osm = "aktiv"
	}
	bridge := "aus"
	if s.Cfg.MqttBroker != "" {
		bridge = fmt.Sprintf("an (TLS=%s)", coalesce(s.Cfg.MqttTLS, "?"))
	}

	body := renderKVBlock([]kvItem{
		{"dfld.yml", conf},
		{"Live", live},
		{"Backfill", bf},
		{"OSM", osm},
		{"Bridge", bridge},
	})
	return stBox.Width(34).Render(titleLine("Konfiguration") + "\n" + body)
}

func boxSummary(s Snapshot) string {
	infraUp, infraTotal := countUp(s.Infra)
	connUp, connTotal := countUp(s.Conn)

	infraStr := fmt.Sprintf("%d/%d laufend", infraUp, infraTotal)
	if infraUp < infraTotal {
		infraStr = lipgloss.NewStyle().Foreground(colWarn).Render(infraStr)
	} else {
		infraStr = stStatusOK.Render(infraStr)
	}
	connStr := fmt.Sprintf("%d/%d laufend", connUp, connTotal)
	if connUp < connTotal {
		connStr = lipgloss.NewStyle().Foreground(colWarn).Render(connStr)
	} else {
		connStr = stStatusOK.Render(connStr)
	}

	body := renderKVBlock([]kvItem{
		{"Infrastructure", infraStr},
		{"Connectors", connStr},
	}) + "\n\n" + stMuted.Render("Details: Ansicht 2")
	return stBox.Width(30).Render(titleLine("Container") + "\n" + body)
}

func boxDisk(s Snapshot, w int) string {
	body := renderKVBlock([]kvItem{
		{"/", s.System.DiskRoot},
		{"/opt/dfld", s.System.DiskDfld},
		{"/boot/firmware", s.System.DiskBoot},
	})
	return stBox.Width(w).Render(titleLine("Disk") + "\n" + body)
}

func boxFlow(s Snapshot, w int) string {
	// spl/min: erwartet 60 bei 1 Hz. <30/min = bad, <54/min = warn.
	splStr := fmt.Sprintf("%d msg/min", s.Spl1m)
	switch {
	case s.Spl1m < 0:
		splStr = lipgloss.NewStyle().Foreground(colBad).Render("error")
	case s.Spl1m < 30:
		splStr = lipgloss.NewStyle().Foreground(colBad).Render(splStr)
	case s.Spl1m < 54:
		splStr = lipgloss.NewStyle().Foreground(colWarn).Render(splStr)
	default:
		splStr = stStatusOK.Render(splStr)
	}

	brokerStr := "—"
	if s.BrokerPerMin >= 0 {
		brokerStr = fmt.Sprintf("%.0f msg/min", s.BrokerPerMin)
	}

	aircraftStr := "—"
	if s.Aircraft >= 0 {
		aircraftStr = fmt.Sprintf("%d sichtbar", s.Aircraft)
	}

	body := renderKVBlock([]kvItem{
		{"spl/min", splStr},
		{"Broker publish", brokerStr},
		{"InfluxDB spl/5m", fmt.Sprintf("%d", s.Spl5m)},
		{"Aircraft sichtbar", aircraftStr},
		{"Flyover/h", fmt.Sprintf("%d", s.FlyH)},
		{"tsdb2http last-tx", s.TxAge},
	})
	return stBox.Width(w).Render(titleLine("Datenfluss") + "\n" + body)
}

func countUp(cs []ContainerStatus) (up, total int) {
	for _, c := range cs {
		if c.Disabled {
			continue
		}
		total++
		if c.State == "running" {
			up++
		}
	}
	return
}

// === Render: Container-Detail ===

func renderContainers(s Snapshot, w int) string {
	header := renderHeader(s, w)
	infra := containerSection("Infrastructure", s.Infra)
	conn := containerSection("Connectors", s.Conn)

	footer := stMuted.Render(" [r] Aktualisieren   [1] Übersicht   [3] Logs   [4] SPL live   [5] Sensoren   [q] Beenden")

	return lipgloss.JoinVertical(lipgloss.Left,
		header,
		infra,
		conn,
		"",
		footer,
	)
}

func containerSection(title string, cs []ContainerStatus) string {
	rows := []string{titleLine(title)}
	for _, c := range cs {
		rows = append(rows, containerRow(c))
	}
	return stBox.Render(strings.Join(rows, "\n"))
}

func containerRow(c ContainerStatus) string {
	glyph := glyphStyle(c.Health).Render(c.Health)

	var name string
	if c.Disabled {
		name = lipgloss.NewStyle().Foreground(colDisabled).Render(padRight(c.Name, 16))
	} else {
		name = padRight(c.Name, 16)
	}

	var state string
	switch c.State {
	case "running":
		state = padRight(fmt.Sprintf("up %s", c.Uptime), 10)
	case "disabled":
		state = stMuted.Render(padRight("disabled", 10))
	default:
		state = lipgloss.NewStyle().Foreground(colBad).Render(padRight(c.State, 10))
	}

	rc := ""
	if !c.Disabled {
		s := fmt.Sprintf("r=%-3d", c.RestartCount)
		if c.RestartCount > 5 {
			rc = lipgloss.NewStyle().Foreground(colWarn).Render(s)
		} else {
			rc = stMuted.Render(s)
		}
	} else {
		rc = stMuted.Render("     ")
	}

	flow := c.Flow
	if !c.Disabled {
		if strings.Contains(flow, "stale") {
			flow = lipgloss.NewStyle().Foreground(colWarn).Render(flow)
		}
	} else {
		flow = stMuted.Render(flow)
	}

	return fmt.Sprintf(" %s %s %s %s  %s", glyph, name, state, rc, flow)
}

// === Render: SPL live chart ===
//
// Y-Achse fest auf [20, 100] dB(A), x-Achse = letzte N Sekunden (= Spalten).
// Pro Sekunde 1 Sample, neueste rechts. Unicode-Block-Chars (1/8-Stufen)
// fuer sub-row-Aufloesung — wirkt fluessiger als ganzzahlige Bars.

const (
	splYMin = 20.0
	splYMax = 100.0
)

var splBlocks = []rune{' ', '▁', '▂', '▃', '▄', '▅', '▆', '▇'}

func renderSplChart(ring *SplRing, w, h int) string {
	const leftWidth = 6 // "100 ┤"
	chartH := h - 5     // Header + Achse + Stats + Footer + 1 Puffer
	if chartH < 5 {
		chartH = 5
	}
	chartW := w - leftWidth - 2
	if chartW < 10 {
		chartW = 10
	}

	// Pulse: Ring auf chartW Spalten begrenzen (rechts-bündig).
	ring.Resize(chartW)
	samples := ring.Values()

	yRange := splYMax - splYMin
	pixelsPerRow := 8
	totalPixels := chartH * pixelsPerRow

	var sb strings.Builder
	sb.WriteString(stTitle.Render(" SPL live  ▸  dfld/sensors/noise/spl"))
	sb.WriteString("\n")

	for row := 0; row < chartH; row++ {
		rowFromBottom := chartH - 1 - row
		// Y-Achsen-Label nur an "runden" Stellen (oben, Mitte, unten) —
		// vermeidet krumme Zahlen wie "84.4 dB".
		var label string
		if row == 0 {
			label = fmt.Sprintf("%4.0f ", splYMax)
		} else if row == chartH-1 {
			label = fmt.Sprintf("%4.0f ", splYMin)
		} else if row == chartH/2 {
			label = fmt.Sprintf("%4.0f ", splYMin+yRange/2)
		} else {
			label = "     "
		}
		sb.WriteString(stMuted.Render(label))
		sb.WriteString(stMuted.Render("┤"))

		// Padding links wenn weniger Samples als Spalten:
		pad := chartW - len(samples)
		if pad > 0 {
			sb.WriteString(strings.Repeat(" ", pad))
		}

		pixelMin := rowFromBottom * pixelsPerRow
		pixelMax := pixelMin + pixelsPerRow
		for _, v := range samples {
			if v < splYMin {
				v = splYMin
			}
			if v > splYMax {
				v = splYMax
			}
			pixel := int((v - splYMin) / yRange * float64(totalPixels))
			if pixel < 0 {
				pixel = 0
			}
			if pixel > totalPixels {
				pixel = totalPixels
			}
			switch {
			case pixel >= pixelMax:
				sb.WriteRune('█')
			case pixel > pixelMin:
				sb.WriteRune(splBlocks[pixel-pixelMin])
			default:
				sb.WriteRune(' ')
			}
		}
		sb.WriteString("\n")
	}

	// x-Achse
	sb.WriteString("     " + stMuted.Render("└"+strings.Repeat("─", chartW)))
	sb.WriteString("\n")
	// Zeit-Markierung (älteste links, jetzt rechts)
	leftMark := fmt.Sprintf(" -%ds", chartW)
	rightMark := "now"
	gap := chartW - len(leftMark) - len(rightMark)
	if gap < 1 {
		gap = 1
	}
	sb.WriteString("     ")
	sb.WriteString(stMuted.Render(leftMark + strings.Repeat(" ", gap) + rightMark))
	sb.WriteString("\n")

	// Stats-Zeile
	if ring.Len() > 0 {
		mn, mx, avg := ring.Stats()
		stats := fmt.Sprintf(" now %.1f dB(A)   min %.1f   max %.1f   avg %.1f   samples %d",
			ring.Last(), mn, mx, avg, ring.Len())
		sb.WriteString(stLabel.Render(stats))
	} else {
		sb.WriteString(stMuted.Render(" (warte auf erste MQTT-Message…)"))
	}
	sb.WriteString("\n")
	sb.WriteString(stMuted.Render(" [1] Übersicht   [2] Container   [3] Logs   [5] Sensoren   [Esc] zurück   [q] Beenden"))

	return sb.String()
}

// === Render: Sensoren-Liste ===

func renderSensors(rows []SensorRow, w, h int) string {
	header := stTitle.Render(" Sensoren-Liste  ▸  dfld/#")
	footerNav := " [1] Übersicht   [2] Container   [3] Logs   [4] SPL   [Esc] zurück   [q] Beenden"
	if len(rows) == 0 {
		return header + "\n" + stMuted.Render("\n (warte auf erste MQTT-Message…)") +
			"\n\n" + stMuted.Render(footerNav)
	}

	// Spaltenbreiten
	topicW := 0
	for _, r := range rows {
		if len(r.Topic) > topicW {
			topicW = len(r.Topic)
		}
	}
	if topicW > 38 {
		topicW = 38
	}
	if topicW < 20 {
		topicW = 20
	}
	ageW := 10
	payloadW := w - topicW - ageW - 6
	if payloadW < 20 {
		payloadW = 20
	}

	var sb strings.Builder
	sb.WriteString(header)
	sb.WriteString("\n")
	// Header-Zeile
	sb.WriteString(stMuted.Render(fmt.Sprintf(" %-*s  %-*s  %s",
		topicW, "Topic", ageW, "Alter", "Wert")))
	sb.WriteString("\n")
	sb.WriteString(stMuted.Render(" " + strings.Repeat("─", topicW+ageW+payloadW+4)))
	sb.WriteString("\n")

	visible := h - 6
	if visible < 1 {
		visible = 1
	}
	if visible > len(rows) {
		visible = len(rows)
	}
	for i := 0; i < visible; i++ {
		r := rows[i]
		topic := truncate(r.Topic, topicW)
		age := humanDur(r.Age) + " ago"
		payload := PrettyPayload(r.Payload, payloadW)
		// Frisch (< 5s) grün, älter > 5min grau
		ageStyle := stStatusOK
		if r.Age > 5*time.Minute {
			ageStyle = stMuted
		} else if r.Age > 5*time.Second {
			ageStyle = stLabel
		}
		sb.WriteString(fmt.Sprintf(" %-*s  %s  %s",
			topicW, topic,
			ageStyle.Render(fmt.Sprintf("%-*s", ageW, age)),
			payload))
		sb.WriteString("\n")
	}

	sb.WriteString("\n")
	sb.WriteString(stMuted.Render(footerNav))
	return sb.String()
}

// === Render: Logs ===

func renderLogs(name, body string, w, h int) string {
	hdr := stTitle.Render(fmt.Sprintf(" Logs: %s ", name))
	footer := stMuted.Render(" [↑/↓] scrollen   [Esc/3] zurück zur Liste   [1] Übersicht   [2] Container   [4] SPL   [5] Sensoren   [q] Beenden")
	logBox := stBox.Width(w - 2).Height(h - 4).Render(body)
	return lipgloss.JoinVertical(lipgloss.Left, hdr, logBox, footer)
}

// === Container-Auswahl für Logs ===

func renderLogPicker(s Snapshot, selected int, w int) string {
	hdr := stTitle.Render(" Logs: Container auswählen ")

	var lines []string
	idx := 0

	// addGroup haengt eine Section mit Header an, wenn mindestens ein
	// enabled Container in der Liste ist. Die Selection-Index-Logik
	// (m.logPickerSel) zaehlt nur Container, keine Header — passt 1:1
	// zur pickerList()-Reihenfolge in main.go.
	addGroup := func(title string, list []ContainerStatus) {
		hasEnabled := false
		for _, c := range list {
			if !c.Disabled {
				hasEnabled = true
				break
			}
		}
		if !hasEnabled {
			return
		}
		if len(lines) > 0 {
			lines = append(lines, "")
		}
		lines = append(lines, stLabel.Render(title))
		for _, c := range list {
			if c.Disabled {
				continue
			}
			marker := "  "
			if idx == selected {
				marker = "▶ "
			}
			glyph := glyphStyle(c.Health).Render(c.Health)
			line := fmt.Sprintf("  %s%s %s", marker, glyph, c.Name)
			if idx == selected {
				line = lipgloss.NewStyle().Foreground(colTitle).Bold(true).Render(line)
			}
			lines = append(lines, line)
			idx++
		}
	}
	addGroup("Infrastructure", s.Infra)
	addGroup("Connectors", s.Conn)

	body := strings.Join(lines, "\n")
	box := stBox.Width(w - 4).Render(body)

	footer := stMuted.Render(" [↑/↓] auswählen   [Enter] anzeigen   [1] Übersicht   [2] Container   [4] SPL   [5] Sensoren   [Esc] zurück")
	return lipgloss.JoinVertical(lipgloss.Left, hdr, box, footer)
}

// === Helpers ===

func titleLine(s string) string {
	return stLabel.Render(s)
}

// kvItem ist ein Label-Wert-Paar für renderKVBlock.
type kvItem struct {
	Label, Value string
}

// renderKVBlock zeichnet eine Liste von Label/Value-Paaren mit Labels
// linksbündig auf gemeinsame Breite (= längstes Label + ":") gepaddet.
// Anders als die vorherige hartcodierte 14-char-Width bekommt jede Box
// damit ihre eigene saubere Ausrichtung.
func renderKVBlock(items []kvItem) string {
	w := 0
	for _, it := range items {
		if l := len(it.Label) + 1; l > w {
			w = l
		}
	}
	lines := make([]string, len(items))
	for i, it := range items {
		lines[i] = fmt.Sprintf("%s  %s",
			stMuted.Render(padRight(it.Label+":", w)),
			it.Value)
	}
	return strings.Join(lines, "\n")
}

// kv ist die Legacy-Helper-API (hartcodierte width=14) — bleibt für
// abgesetzte Renderings (Logs/Sensoren) verfügbar.
func kv(label, value string) string {
	return fmt.Sprintf("%s  %s", stMuted.Render(padRight(label+":", 14)), value)
}

func padRight(s string, n int) string {
	w := lipgloss.Width(s)
	if w >= n {
		return s
	}
	return s + strings.Repeat(" ", n-w)
}

func trim(s string, n int) string {
	if len(s) <= n {
		return s
	}
	if n <= 1 {
		return s[:n]
	}
	return s[:n-1] + "…"
}

func coalesce(s, fallback string) string {
	if s == "" {
		return fallback
	}
	return s
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
