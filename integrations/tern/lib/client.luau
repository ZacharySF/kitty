--!strict
-- The window half's connection to rika!: finding the running rika!, pairing, keeping the
-- token and sending requests. Everything Tern provides comes in through `deps`, so the unit
-- tests (tests/client_test.luau) drive it with fakes.
--
-- The token lives in Tern's keychain store (`tern.secrets`, window halves only). Only when
-- that API is missing or the store reports a failure does it fall back to `tern.kv`, a plain
-- file in the plugin's data folder that any program running as the user can read; the
-- fallback is announced once with a toast.

local core = require("./core")

type Outcome = core.Outcome
type Endpoint = core.Endpoint
type Done = (outcome: Outcome, cx: any) -> ()

export type Deps = {
	fetch: (url: string, opts: { [string]: any }, cb: (result: any, cx: any) -> ()) -> (),
	json: { encode: (any) -> string, decode: (string) -> any },
	kv: { get: (string) -> any, set: (string, any) -> () },
	secrets: { get: (string) -> any, set: (string, string?) -> any }?,
	read_file: (path: string, max: number) -> string,
	timer: (ms: number, fn: (cx: any) -> ()) -> any,
	now: () -> number,
	plugin_dir: string,
}

local POLL_MS = 2000
local KEYCHAIN_WAIT_MS = 15000
local TIMEOUT_MS = 10000
local MAX_ENDPOINT = 4096

local Client = {}
Client.__index = Client

export type Client = typeof(setmetatable(
	{} :: {
		deps: Deps,
		token: string?,
		rejected: string?,
		pairing: { request: string, code: string, deadline: number, waiters: { Done }, origin: string }?,
		on_pairing: (cx: any, code: string, seconds: number) -> (),
		on_paired: (cx: any) -> (),
		on_notice: (cx: any, level: string, text: string, sub: string?) -> (),
	},
	Client
))

local function noop() end

function Client.new(deps: Deps): Client
	local self = setmetatable({
		deps = deps,
		token = nil,
		rejected = nil,
		pairing = nil,
		on_pairing = noop :: any,
		on_paired = noop :: any,
		on_notice = noop :: any,
	}, Client)
	return self
end

local function outcome(kind: string, message: string): Outcome
	return { kind = kind, status = 0, data = nil, message = message }
end

--- The running rika!'s address, or why there is none.
function Client.endpoint(self: Client): (Endpoint?, Outcome?)
	local path = core.endpoint_path(self.deps.plugin_dir)
	if not path then
		return nil, outcome("unconfigured", "This copy of the plugin was not installed by rika!. Install it from rika!: Tern → rika! plugin for Tern.")
	end
	local ok, text = pcall(self.deps.read_file, path, MAX_ENDPOINT)
	if not ok then
		return nil, outcome("offline", "rika! isn't running. Start rika!, then try again.")
	end
	local parsed, decoded = pcall(self.deps.json.decode, text)
	local endpoint, problem = core.parse_endpoint(if parsed then decoded else nil)
	if not endpoint then
		return nil, outcome("offline", problem or "rika!'s endpoint file is not readable")
	end
	return endpoint, nil
end

--- The workspace this window's token belongs to: rika!'s private folder.
function Client.workspace(self: Client): string?
	return core.private_dir(self.deps.plugin_dir)
end

local function decode(json, body: string): any
	if body == "" then
		return nil
	end
	local ok, value = pcall(json.decode, body)
	return if ok then value else nil
end

--- One HTTP exchange; `done` gets the classified outcome and the callback's fresh cx.
function Client.send(self: Client, endpoint: Endpoint, method: string, path: string, body: any, token: string?, done: Done)
	local headers: { [string]: string } = { Accept = "application/json" }
	local opts: { [string]: any } = { method = method, headers = headers, timeout_ms = TIMEOUT_MS }
	if body ~= nil then
		headers["Content-Type"] = "application/json"
		opts.body = self.deps.json.encode(body)
	end
	if token then
		headers.Authorization = "Bearer " .. token
	end
	self.deps.fetch(endpoint.origin .. path, opts, function(result, cx)
		local data = decode(self.deps.json, result.body or "")
		done(core.classify(result.status or 0, result.error, data), cx)
	end)
end

-- Token storage ---------------------------------------------------------------

--- The stored token for this workspace, from memory, the keychain or the fallback file.
function Client.load(self: Client, cx: any, k: (token: string?, problem: Outcome?, cx: any) -> ())
	if self.token then
		k(self.token, nil, cx)
		return
	end
	local kv = self.deps.kv
	if kv.get("workspace") ~= self:workspace() then
		k(nil, nil, cx)
		return
	end
	local secrets = self.deps.secrets
	if not secrets then
		local value = kv.get("token")
		self.token = if type(value) == "string" and value ~= self.rejected then value else nil
		k(self.token, nil, cx)
		return
	end
	local settled = false
	local waiting = self.deps.timer(KEYCHAIN_WAIT_MS, function(later)
		if not settled then
			settled = true
			k(nil, outcome("keychain", "The keychain did not answer; unlock it and try again."), later)
		end
	end)
	secrets.get("token"):next(function(value, err, later)
		if settled then
			return
		end
		settled = true
		if waiting and waiting.cancel then
			waiting:cancel()
		end
		if err then
			k(nil, outcome("keychain", "rika!'s token is in the keychain, which is locked: " .. tostring(err)), later)
			return
		end
		if type(value) ~= "string" then
			local fallback = kv.get("token")
			value = if type(fallback) == "string" then fallback else nil
		end
		-- A keychain delete still in flight must not bring back the token rika! refused.
		if value == self.rejected then
			value = nil
		end
		self.token = value
		k(value, nil, later)
	end)
end

--- Keep a new token: the keychain when there is one, else the plugin's data file.
function Client.store(self: Client, cx: any, token: string)
	local kv = self.deps.kv
	self.token = token
	kv.set("workspace", self:workspace())
	local secrets = self.deps.secrets
	local function fallback(later: any, reason: string)
		kv.set("token", token)
		kv.set("token_store", "kv")
		self.on_notice(later, "info", "rika! token kept in Tern's plugin data", reason)
	end
	if not secrets then
		fallback(cx, "This Tern has no keychain access for plugins, so the token is in kv.json, readable by programs running as you.")
		return
	end
	secrets.set("token", token):next(function(_, err, later)
		if err then
			fallback(later, "The keychain refused it (" .. tostring(err) .. "), so the token is in kv.json, readable by programs running as you.")
		else
			kv.set("token", nil)
			kv.set("token_store", "keychain")
		end
	end)
end

--- Drop a token rika! no longer accepts.
function Client.forget(self: Client, token: string)
	self.token = nil
	self.rejected = token
	self.deps.kv.set("token", nil)
	local secrets = self.deps.secrets
	if secrets then
		secrets.set("token", nil):next(noop)
	end
end

-- Pairing ---------------------------------------------------------------------

local function settle(self: Client, result: Outcome?, cx: any)
	local pairing = self.pairing
	self.pairing = nil
	if not pairing then
		return
	end
	for _, waiter in pairing.waiters do
		waiter(result or outcome("ok", ""), cx)
	end
end

function Client.poll(self: Client, endpoint: Endpoint)
	local pairing = self.pairing
	if not pairing then
		return
	end
	self:send(endpoint, "POST", endpoint.pairing .. "/status", { request = pairing.request }, nil, function(answer, cx)
		if self.pairing ~= pairing then
			return
		end
		local status = if answer.kind == "ok" and type(answer.data) == "table" then answer.data.status else nil
		if status == "approved" and type(answer.data.token) == "string" then
			self:store(cx, answer.data.token)
			self.on_paired(cx)
			settle(self, nil, cx)
		elseif status == "denied" then
			settle(self, outcome("denied", "rika! denied the pairing request."), cx)
		elseif status == "expired" or self.deps.now() > pairing.deadline then
			settle(self, outcome("expired", "The pairing request expired before it was approved in rika!."), cx)
		elseif answer.kind == "disabled" then
			settle(self, answer, cx)
		else
			-- Pending, or rika! briefly unreachable: ask again until the request expires.
			self.deps.timer(POLL_MS, function()
				self:poll(endpoint)
			end)
		end
	end)
end

--- Ask rika! to pair this Tern. `waiter` runs once the user approved (outcome kind "ok")
--- or the request failed; a request already waiting is shared.
function Client.pair(self: Client, cx: any, endpoint: Endpoint, waiter: Done)
	local pairing = self.pairing
	if pairing and pairing.origin == endpoint.origin then
		table.insert(pairing.waiters, waiter)
		self.on_pairing(cx, pairing.code, math.max(0, math.floor((pairing.deadline - self.deps.now()) / 1000)))
		return
	end
	local body = { name = core.CLIENT_NAME, kind = core.KIND, scopes = core.SCOPES }
	self:send(endpoint, "POST", endpoint.pairing, body, nil, function(answer, later)
		local data = answer.data
		if answer.kind ~= "ok" or type(data) ~= "table" or type(data.request) ~= "string" or type(data.code) ~= "string" then
			waiter(if answer.kind == "ok" then outcome("failed", "rika! answered the pairing request oddly") else answer, later)
			return
		end
		local seconds = if type(data.expires_in) == "number" then data.expires_in else 300
		self.pairing = {
			request = data.request,
			code = data.code,
			deadline = self.deps.now() + seconds * 1000,
			waiters = { waiter },
			origin = endpoint.origin,
		}
		self.on_pairing(later, data.code, seconds)
		self.deps.timer(POLL_MS, function()
			self:poll(endpoint)
		end)
	end)
end

-- Requests --------------------------------------------------------------------

--- Send one authenticated request, pairing first when there is no token and once more
--- when rika! no longer accepts it.
function Client.request(self: Client, cx: any, method: string, path: string, body: any, done: Done)
	local endpoint, problem = self:endpoint()
	if not endpoint then
		done(problem :: Outcome, cx)
		return
	end
	local target = endpoint :: Endpoint
	local function send(token: string, later: any, repaired: boolean)
		self:send(target, method, path, body, token, function(answer, after)
			if answer.kind == "unauthorized" and not repaired then
				self:forget(token)
				self:pair(after, target, function(paired, again)
					if paired.kind ~= "ok" then
						done(paired, again)
					elseif self.token then
						send(self.token, again, true)
					else
						done(outcome("unauthorized", "rika! did not accept the new pairing"), again)
					end
				end)
				return
			end
			done(answer, after)
		end)
	end
	self:load(cx, function(token, trouble, later)
		if trouble then
			done(trouble, later)
		elseif token then
			send(token, later, false)
		else
			self:pair(later, target, function(paired, after)
				if paired.kind ~= "ok" then
					done(paired, after)
				elseif self.token then
					send(self.token, after, true)
				else
					done(outcome("unauthorized", "rika! did not accept the new pairing"), after)
				end
			end)
		end
	end)
end

return Client
