// Styling + Render-Funktionen mit lipgloss.

package main

import (
	"fmt"
	"strings"

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

	footer := stMuted.Render(" [r] Aktualisieren   [2] Container-Detail   [3] Logs   [q] Beenden")

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
	body := lipgloss.JoinVertical(lipgloss.Left,
		kv("Modell", trim(s.System.PiModel, 22)),
		kv("CPU", s.System.CPUTemp),
		kv("Uptime", s.System.Uptime),
		kv("RAM", s.System.Mem),
		kv("WLAN", coalesce(s.System.WlanRSSI, "—")),
	)
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

	body := lipgloss.JoinVertical(lipgloss.Left,
		kv("dfld.yml", conf),
		kv("Live", live),
		kv("Backfill", bf),
		kv("OSM", osm),
		kv("Bridge", bridge),
	)
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

	body := lipgloss.JoinVertical(lipgloss.Left,
		kv("Infrastructure", infraStr),
		kv("Connectors", connStr),
		"",
		stMuted.Render("Details: Ansicht 2"),
	)
	return stBox.Width(30).Render(titleLine("Container") + "\n" + body)
}

func boxDisk(s Snapshot, w int) string {
	body := lipgloss.JoinVertical(lipgloss.Left,
		kv("/", s.System.DiskRoot),
		kv("/opt/dfld", s.System.DiskDfld),
		kv("/boot/firmware", s.System.DiskBoot),
	)
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

	body := lipgloss.JoinVertical(lipgloss.Left,
		kv("spl/min", splStr),
		kv("Broker publish", brokerStr),
		kv("InfluxDB spl/5m", fmt.Sprintf("%d", s.Spl5m)),
		kv("Aircraft sichtbar", aircraftStr),
		kv("Flyover/h", fmt.Sprintf("%d", s.FlyH)),
		kv("tsdb2http last-tx", s.TxAge),
	)
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

	footer := stMuted.Render(" [r] Aktualisieren   [1] Übersicht   [3] Logs   [q] Beenden")

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

// === Render: Logs ===

func renderLogs(name, body string, w, h int) string {
	hdr := stTitle.Render(fmt.Sprintf(" Logs: %s ", name))
	footer := stMuted.Render(" [↑/↓] scrollen   [Esc] zurück   [q] Beenden")
	logBox := stBox.Width(w - 2).Height(h - 4).Render(body)
	return lipgloss.JoinVertical(lipgloss.Left, hdr, logBox, footer)
}

// === Container-Auswahl für Logs ===

func renderLogPicker(s Snapshot, selected int, w int) string {
	hdr := stTitle.Render(" Logs: Container auswählen ")

	var items []string
	all := append([]ContainerStatus{}, s.Infra...)
	all = append(all, s.Conn...)
	for i, c := range all {
		if c.Disabled {
			continue
		}
		marker := "  "
		if i == selected {
			marker = "▶ "
		}
		glyph := glyphStyle(c.Health).Render(c.Health)
		line := fmt.Sprintf("%s%s %s", marker, glyph, c.Name)
		if i == selected {
			line = lipgloss.NewStyle().Foreground(colTitle).Bold(true).Render(line)
		}
		items = append(items, line)
	}

	body := strings.Join(items, "\n")
	box := stBox.Width(w - 4).Render(body)

	footer := stMuted.Render(" [↑/↓] auswählen   [Enter] anzeigen   [Esc] zurück")
	return lipgloss.JoinVertical(lipgloss.Left, hdr, box, footer)
}

// === Helpers ===

func titleLine(s string) string {
	return stLabel.Render(s)
}

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
