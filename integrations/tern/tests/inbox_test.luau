--!nonstrict
local inbox = require("../lib/inbox")

-- tern.ui stand-ins that keep what the view asked for.
local ui = {}
function ui.span(t: string, s: string?)
	return { t = t, s = s }
end
function ui.text(spans: any)
	return { k = "text", p = { spans = spans } }
end
function ui.col(children: any)
	return { k = "col", c = children }
end
function ui.row(children: any)
	return { k = "row", c = children }
end
function ui.card(head: any, children: any)
	return { k = "card", p = { head = head }, c = children }
end
function ui.section(head: any, children: any)
	return { k = "section", p = { head = head }, c = children }
end
function ui.badge(t: string, tone: string)
	return { k = "badge", p = { text = t, tone = tone } }
end
function ui.list(items: any)
	local children = {}
	for i, item in items do
		children[i] = { k = "item", p = { label = item.label, detail = item.detail } }
	end
	return { k = "list", c = children }
end

local ORIGIN = "http://127.0.0.1:8770"

local function payload()
	return {
		state = "ok",
		origin = ORIGIN,
		recent = {
			inbox = 4,
			pages = { { number = 9, title = "Plans", updated_at = "2026-11-02T10:00:00+00:00" }, { number = 4, title = "Inbox", updated_at = "2026-11-01T09:00:00+00:00" } },
			tasks = { { id = "t-1", title = "Call back", due = "2026-11-03" }, { id = "t-2", title = "", due = nil } },
		},
	}
end

local function find(node: any, kind: string, found: { any }?): { any }
	local out = found or {}
	if type(node) == "table" then
		if node.k == kind then
			table.insert(out, node)
		end
		for _, child in node.c or {} do
			find(child, kind, out)
		end
	end
	return out
end

return function(t: any)
	t.test("reads recent pages and open tasks with their rika! links", function()
		local state = inbox.new()
		inbox.apply(state, payload())
		t.eq(state.phase, "ok")
		t.eq(state.pages[1], { key = "p9", label = "Plans", detail = "2026-11-02", url = ORIGIN .. "/#notes/9" })
		t.eq(state.pages[2].detail, "Inbox · 2026-11-01")
		t.eq(state.tasks[1], { key = "tt-1", label = "Call back", detail = "due 2026-11-03", url = ORIGIN .. "/#calendar" })
		t.eq(state.tasks[2].label, "Untitled task")
		t.eq(inbox.title(state), "rika! Inbox (2)")
	end)

	t.test("lists the Inbox page first when it was not edited recently", function()
		local state = inbox.new()
		local data = payload()
		data.recent.pages = { { number = 9, title = "Plans", updated_at = "2026-11-02" } }
		inbox.apply(state, data)
		t.eq(state.pages[1], { key = "p4", label = "Inbox", detail = "Inbox", url = ORIGIN .. "/#notes/4" })
	end)

	t.test("ignores data naming an address that is not this computer", function()
		local state = inbox.new()
		local data = payload()
		data.origin = "https://evil.example"
		inbox.apply(state, data)
		t.eq(state.phase, "loading")
		t.eq(#state.pages, 0)
	end)

	t.test("shows pairing and errors from the window half", function()
		local state = inbox.new()
		inbox.apply(state, { state = "pairing", code = "ABC-DEF" })
		t.eq(state.phase, "pairing")
		local view = inbox.view(state, ui)
		t.eq(#find(view.main, "card"), 1)
		inbox.apply(state, { state = "error", message = "rika! isn't running." })
		t.eq(state.phase, "error")
		t.eq(state.message, "rika! isn't running.")
		inbox.apply(state, { state = "error" })
		t.eq(state.message, "rika! could not be reached")
	end)

	t.test("renders keyed, clickable rows with the selection", function()
		local state = inbox.new()
		inbox.apply(state, payload())
		local view = inbox.view(state, ui)
		local lists = find(view.main, "list")
		t.eq(#lists, 2)
		t.eq(lists[1].p.selected, "main.pages.items.p9")
		t.eq(lists[1].c[1].p.actions, { click = "open=p9" })
		t.eq(lists[1].c[1].p.key, "p9")
		t.eq(lists[2].p.selected, nil)
		state.sel = 3
		local again = find(inbox.view(state, ui).main, "list")
		t.eq(again[2].p.selected, "main.tasks.items.tt-1")
		local badges = find(view.dock, "badge")
		t.eq(badges[1].p.actions, { click = "refresh" })
	end)

	t.test("turns clicks and keys into refreshes and links", function()
		local state = inbox.new()
		inbox.apply(state, payload())
		t.eq(inbox.event(state, { ev = "action", act = "refresh" }), { refresh = true })
		t.eq(inbox.event(state, { ev = "action", act = "open", value = "p4" }), { open = ORIGIN .. "/#notes/4" })
		t.eq(inbox.event(state, { ev = "action", act = "open", value = "p404" }), nil)
		t.eq(inbox.event(state, { ev = "action", act = "open-kitty" }), { open = ORIGIN .. "/" })
		t.eq(inbox.event(state, { ev = "select" }), nil)
		local changed, effect = inbox.key(state, { name = "j" })
		t.eq(changed, true)
		t.eq(effect, nil)
		t.eq(state.sel, 2)
		local _, open = inbox.key(state, { name = "enter" })
		t.eq(open, { open = ORIGIN .. "/#notes/4" })
		for _ = 1, 10 do
			inbox.key(state, { name = "down" })
		end
		t.eq(state.sel, 4, "stops at the last task")
		t.eq(select(2, inbox.key(state, { name = "r" })), { refresh = true })
		t.eq((inbox.key(state, { name = "x" })), false)
	end)
end
