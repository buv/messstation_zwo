// dfld-status — Status-TUI für eine DFLD-Messstation.
//
// bubbletea-App mit drei Screens (Übersicht / Container / Logs) und
// einem Hintergrund-Refresh alle 2s. Daten-Collection in collect.go,
// Render-Code in ui.go.
//
// Read-only: keine Aktionen auf der Pi ausser docker inspect / logs.

package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

type screen int

const (
	screenOverview screen = iota
	screenContainers
	screenLogPicker
	screenLogView
	screenSpl
	screenSensors
)

// === Messages ===

type tickMsg time.Time
type snapMsg Snapshot
type logsMsg struct {
	name string
	body string
}
type mqttRecvMsg MqttMsg
type mqttDoneMsg struct{}

func tickCmd() tea.Cmd {
	// 5s statt 2s: dfld-status soll READ-ONLY und LIGHT sein. Pi Zero 2W
	// mit 4 langsamen Cores + SD-Karten-IO ist mit 2s-Tick und parallelen
	// docker-/influx-/mosquitto-Probes leicht zu ueberlasten. 5s gibt
	// genug Headroom plus ist fuer ein Status-Tool ohnehin schnell genug.
	return tea.Tick(5*time.Second, func(t time.Time) tea.Msg { return tickMsg(t) })
}

func snapCmd() tea.Cmd {
	return func() tea.Msg { return snapMsg(collectSnapshot()) }
}

func logsCmd(name string) tea.Cmd {
	return func() tea.Msg {
		return logsMsg{name: name, body: containerLogs(name, 300)}
	}
}

// nextMqttCmd liest die naechste Message aus dem Stream-Channel.
// bubbletea ruft tea.Cmd in einer goroutine; das Lesen blockiert bis
// eine Message kommt, was natuerlich rate-limited (1 Hz spl, andere
// seltener). Nach jeder Msg wird die Cmd in Update neu gestartet.
func nextMqttCmd(stream *MqttStream) tea.Cmd {
	if stream == nil {
		return nil
	}
	return func() tea.Msg {
		msg, ok := <-stream.Msgs
		if !ok {
			return mqttDoneMsg{}
		}
		return mqttRecvMsg(msg)
	}
}

// === Model ===

type model struct {
	width, height int

	current  screen
	snap     Snapshot
	loading  bool
	firstSet bool

	// snapshotting verhindert ueberlappende collectSnapshot-Laeufe.
	// Auf langsamen oder ueberlasteten Pis kann ein Snapshot durchaus
	// 10-30s brauchen; ohne diesen Guard startet jede 2s-Tick einen
	// weiteren parallelen Snapshot — die Pi geht in Subprozess-Explosion.
	snapshotting bool

	logPickerSel int
	logName      string
	logBody      string
	logScroll    int

	// Live-MQTT-Stream wird zentral im Hintergrund gehalten, beliefert
	// SPL-Chart und Sensoren-Liste simultan.
	stream  *MqttStream
	splRing *SplRing
	sensors *SensorState
}

func newModel() model {
	return model{
		current: screenOverview,
		loading: true,
		// snapshotting initial true: Init() startet den ersten snapCmd,
		// jede Tick davor wuerde sonst einen ueberlappenden Snapshot
		// starten. Wird durch den ersten snapMsg auf false geflippt.
		snapshotting: true,
		splRing:      NewSplRing(80),
		sensors:      NewSensorState(),
	}
}

// startStream ist als Helper auf einer Kopie (model value) implementiert —
// das passt zum value-receiver-Pattern von Init/Update/View. Der MqttStream
// wird als Pointer im model-Feld gespeichert; eine zugewiesene Wert-Kopie
// teilt den selben Stream.
func startStream(m model) (model, tea.Cmd) {
	if m.stream != nil {
		return m, nil
	}
	s, err := StartMqttStream()
	if err != nil {
		return m, nil
	}
	m.stream = s
	return m, nextMqttCmd(s)
}

func (m model) Init() tea.Cmd {
	// startStream wird im Update bei der ersten WindowSize-Msg gestartet,
	// damit wir die Terminal-Breite fuer den Ringpuffer kennen. Hier nur
	// initiale Snapshot + Tick.
	return tea.Batch(snapCmd(), tickCmd())
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {

	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		// Ring-Kapazitaet an Chart-Breite anpassen (gross genug fuer
		// breite Terminals, der renderSplChart resized bei Bedarf nach unten).
		if m.width > 20 {
			m.splRing.Resize(m.width - 10)
		}
		// Start des MQTT-Streams einmalig nach erster WindowSize.
		var cmd tea.Cmd
		m, cmd = startStream(m)
		return m, cmd

	case tickMsg:
		// Im Logs-View und in Live-Streams nicht refreshen — die haben
		// eigene Daten-Quellen oder Operator liest gerade.
		if m.current == screenLogView {
			return m, tickCmd()
		}
		// Verhindert Overlap-Stapel: solange ein Snapshot laeuft, kommen
		// keine neuen. Auf langsamen Stationen wird der Refresh dadurch
		// "as-fast-as-possible" statt fix 2s — robust gegen Pi-Schwergang.
		if m.snapshotting {
			return m, tickCmd()
		}
		m.snapshotting = true
		return m, tea.Batch(snapCmd(), tickCmd())

	case snapMsg:
		m.snap = Snapshot(msg)
		m.loading = false
		m.firstSet = true
		m.snapshotting = false
		return m, nil

	case logsMsg:
		m.logName = msg.name
		m.logBody = msg.body
		m.logScroll = 0
		m.current = screenLogView
		return m, nil

	case mqttRecvMsg:
		// Live-Daten verarbeiten — SPL-Topic fuer den Chart, alle
		// Topics fuer die Sensoren-Liste UND den TopicCounter (der
		// von flowFor() statt mosquitto_sub-Subprozessen genutzt wird).
		mm := MqttMsg(msg)
		if mm.Topic == "dfld/sensors/noise/spl" {
			if v, ok := extractDbAavg(mm.Payload); ok {
				m.splRing.Push(v)
			}
		}
		m.sensors.Update(mm.Topic, mm.Payload, mm.Time)
		topicCounter.Record(mm.Topic, mm.Time)
		return m, nextMqttCmd(m.stream)

	case mqttDoneMsg:
		// Stream beendet (z.B. mosquitto_sub gestorben). Nicht
		// automatisch neu starten — wuerde unkontrolliert respawnen.
		// Operator sieht das daran, dass Werte stehen bleiben.
		m.stream = nil
		return m, nil

	case tea.KeyMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

// extractDbAavg liest aus dem JSON-Payload des spl-Topics den
// dB_A_avg-Wert. Robust gegen unerwartete Payloads (returnt ok=false).
func extractDbAavg(payload string) (float64, bool) {
	var p struct {
		DbAavg *float64 `json:"dB_A_avg"`
	}
	if err := json.Unmarshal([]byte(payload), &p); err != nil {
		return 0, false
	}
	if p.DbAavg == nil {
		return 0, false
	}
	return *p.DbAavg, true
}

func (m model) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch m.current {

	case screenOverview:
		switch msg.String() {
		case "q", "ctrl+c", "esc":
			m.stream.Stop()
			return m, tea.Quit
		case "r":
			m.loading = true
			return m, snapCmd()
		case "2":
			m.current = screenContainers
			return m, nil
		case "3", "l":
			m.current = screenLogPicker
			m.logPickerSel = 0
			return m, nil
		case "4":
			m.current = screenSpl
			return m, nil
		case "5":
			m.current = screenSensors
			return m, nil
		}

	case screenContainers:
		switch msg.String() {
		case "q", "ctrl+c":
			m.stream.Stop()
			return m, tea.Quit
		case "esc", "1":
			m.current = screenOverview
			return m, nil
		case "r":
			m.loading = true
			return m, snapCmd()
		case "3", "l":
			m.current = screenLogPicker
			m.logPickerSel = 0
			return m, nil
		case "4":
			m.current = screenSpl
			return m, nil
		case "5":
			m.current = screenSensors
			return m, nil
		}

	case screenSpl, screenSensors:
		switch msg.String() {
		case "q", "ctrl+c":
			m.stream.Stop()
			return m, tea.Quit
		case "esc", "1":
			m.current = screenOverview
			return m, nil
		case "2":
			m.current = screenContainers
			return m, nil
		case "4":
			m.current = screenSpl
			return m, nil
		case "5":
			m.current = screenSensors
			return m, nil
		}

	case screenLogPicker:
		all := m.pickerList()
		switch msg.String() {
		case "q", "ctrl+c":
			m.stream.Stop()
			return m, tea.Quit
		case "esc":
			m.current = screenOverview
			return m, nil
		case "1":
			m.current = screenOverview
			return m, nil
		case "2":
			m.current = screenContainers
			return m, nil
		case "4":
			m.current = screenSpl
			return m, nil
		case "5":
			m.current = screenSensors
			return m, nil
		case "up", "k":
			if m.logPickerSel > 0 {
				m.logPickerSel--
			}
			return m, nil
		case "down", "j":
			if m.logPickerSel < len(all)-1 {
				m.logPickerSel++
			}
			return m, nil
		case "enter":
			if m.logPickerSel < len(all) {
				return m, logsCmd(all[m.logPickerSel])
			}
			return m, nil
		}

	case screenLogView:
		lines := strings.Split(m.logBody, "\n")
		maxScroll := maxInt(0, len(lines)-(m.height-6))
		switch msg.String() {
		case "q", "ctrl+c":
			m.stream.Stop()
			return m, tea.Quit
		case "esc":
			m.current = screenLogPicker
			return m, nil
		case "1":
			m.current = screenOverview
			return m, nil
		case "2":
			m.current = screenContainers
			return m, nil
		case "3":
			m.current = screenLogPicker
			return m, nil
		case "4":
			m.current = screenSpl
			return m, nil
		case "5":
			m.current = screenSensors
			return m, nil
		case "up", "k":
			if m.logScroll > 0 {
				m.logScroll--
			}
			return m, nil
		case "down", "j":
			if m.logScroll < maxScroll {
				m.logScroll++
			}
			return m, nil
		case "pgup":
			m.logScroll -= 10
			if m.logScroll < 0 {
				m.logScroll = 0
			}
			return m, nil
		case "pgdown":
			m.logScroll += 10
			if m.logScroll > maxScroll {
				m.logScroll = maxScroll
			}
			return m, nil
		case "home", "g":
			m.logScroll = 0
			return m, nil
		case "end", "G":
			m.logScroll = maxScroll
			return m, nil
		}
	}
	return m, nil
}

func (m model) pickerList() []string {
	var out []string
	for _, c := range m.snap.Infra {
		if !c.Disabled {
			out = append(out, c.Name)
		}
	}
	for _, c := range m.snap.Conn {
		if !c.Disabled {
			out = append(out, c.Name)
		}
	}
	return out
}

func (m model) View() string {
	if !m.firstSet {
		return "\n  Sammle Status ...\n"
	}
	switch m.current {
	case screenOverview:
		return renderOverview(m.snap, m.width)
	case screenContainers:
		return renderContainers(m.snap, m.width)
	case screenLogPicker:
		return renderLogPicker(m.snap, m.logPickerSel, m.width)
	case screenLogView:
		lines := strings.Split(m.logBody, "\n")
		visibleH := maxInt(5, m.height-6)
		end := m.logScroll + visibleH
		if end > len(lines) {
			end = len(lines)
		}
		body := strings.Join(lines[m.logScroll:end], "\n")
		return renderLogs(m.logName, body, m.width, m.height)
	case screenSpl:
		return renderSplChart(m.splRing, m.width, m.height)
	case screenSensors:
		return renderSensors(m.sensors.Snapshot(), m.width, m.height)
	}
	return ""
}

// === Entry-Point ===

func main() {
	p := tea.NewProgram(newModel(), tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		fmt.Fprintln(os.Stderr, "fehler:", err)
		os.Exit(1)
	}
}
