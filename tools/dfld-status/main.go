// dfld-status — Status-TUI für eine DFLD-Messstation.
//
// bubbletea-App mit drei Screens (Übersicht / Container / Logs) und
// einem Hintergrund-Refresh alle 2s. Daten-Collection in collect.go,
// Render-Code in ui.go.
//
// Read-only: keine Aktionen auf der Pi ausser docker inspect / logs.

package main

import (
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
)

// === Messages ===

type tickMsg time.Time
type snapMsg Snapshot
type logsMsg struct {
	name string
	body string
}

func tickCmd() tea.Cmd {
	return tea.Tick(2*time.Second, func(t time.Time) tea.Msg { return tickMsg(t) })
}

func snapCmd() tea.Cmd {
	return func() tea.Msg { return snapMsg(collectSnapshot()) }
}

func logsCmd(name string) tea.Cmd {
	return func() tea.Msg {
		return logsMsg{name: name, body: containerLogs(name, 300)}
	}
}

// === Model ===

type model struct {
	width, height int

	current  screen
	snap     Snapshot
	loading  bool
	firstSet bool

	logPickerSel int
	logName      string
	logBody      string
	logScroll    int
}

func newModel() model {
	return model{
		current: screenOverview,
		loading: true,
	}
}

func (m model) Init() tea.Cmd {
	return tea.Batch(snapCmd(), tickCmd())
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {

	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		return m, nil

	case tickMsg:
		// Im Logs-View nicht refreshen — Operator liest gerade.
		if m.current == screenLogView {
			return m, tickCmd()
		}
		return m, tea.Batch(snapCmd(), tickCmd())

	case snapMsg:
		m.snap = Snapshot(msg)
		m.loading = false
		m.firstSet = true
		return m, nil

	case logsMsg:
		m.logName = msg.name
		m.logBody = msg.body
		m.logScroll = 0
		m.current = screenLogView
		return m, nil

	case tea.KeyMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m model) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch m.current {

	case screenOverview:
		switch msg.String() {
		case "q", "ctrl+c", "esc":
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
		}

	case screenContainers:
		switch msg.String() {
		case "q", "ctrl+c":
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
		}

	case screenLogPicker:
		all := m.pickerList()
		switch msg.String() {
		case "q", "ctrl+c":
			return m, tea.Quit
		case "esc":
			m.current = screenOverview
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
			return m, tea.Quit
		case "esc":
			m.current = screenLogPicker
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
