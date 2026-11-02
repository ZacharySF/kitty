--!strict
-- Host half (runs in Tern's session daemon): the "kitty! Inbox" block.
--
-- Host halves have no keychain (`tern.secrets` is window-only), so the block never holds
-- kitty!'s token and never calls kitty!. It asks a Tern window for its data through a
-- `kitty-tern://inbox?pane=N` link, which the window half claims (window.luau), and shows
-- what comes back. A block on screen only where no plugin window runs (Tern's web client)
-- says so after a few tries instead of waiting forever.

local core = require("./lib/core")
local inbox = require("./lib/inbox")

local ui = tern.ui
local RETRY_MS = 2500

local function running(pane: number): boolean
	for _, info in tern.pane.list() do
		if info.pane == pane then
			return true
		end
	end
	return false
end

-- Ask the window half for data, again every RETRY_MS while nothing came back.
local function ask(state: inbox.State, cx: BlockCx, round: number)
	if state.round ~= round or state.phase ~= "loading" or not running(cx.pane) then
		return
	end
	if state.tries >= inbox.MAX_TRIES then
		state.phase = "no-window"
		cx:render()
		return
	end
	state.tries += 1
	cx:open(core.inbox_link(cx.pane))
	tern.timer(RETRY_MS, function()
		ask(state, cx, round)
	end)
end

local function refresh(state: inbox.State, cx: BlockCx)
	state.phase, state.message, state.tries = "loading", "", 0
	state.round += 1
	ask(state, cx, state.round)
end

local function perform(state: inbox.State, effect: inbox.Effect?, cx: BlockCx)
	if not effect then
		return
	end
	if effect.refresh then
		refresh(state, cx)
	elseif effect.open then
		cx:open(effect.open)
	end
end

local block: BlockDef<inbox.State> = {
	init = function(cx, _args, _saved)
		local state = inbox.new()
		refresh(state, cx)
		return state
	end,
	title = function(state)
		return inbox.title(state)
	end,
	view = function(state, _cx)
		return inbox.view(state, ui)
	end,
	key = function(state, key, cx)
		local changed, effect = inbox.key(state, key)
		perform(state, effect, cx)
		return changed or effect ~= nil
	end,
	event = function(state, ev, cx)
		if ev.ev == "action" and ev.act == inbox.ACTION then
			local ok, payload = pcall(tern.json.decode, if type(ev.value) == "string" then ev.value else "")
			if ok then
				inbox.apply(state, payload)
			end
			return
		end
		perform(state, inbox.event(state, ev), cx)
	end,
}

tern.block.define("inbox", block)
