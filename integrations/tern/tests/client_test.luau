--!nonstrict
local Client = require("../lib/client")
local support = require("./support")

local DIR = "/home/u/ws/.private/tern/plugin"
local ENDPOINT = "/home/u/ws/.private/endpoint.json"
local ORIGIN = "http://127.0.0.1:8770"

-- A fake rika! behind tern.fetch: `routes["METHOD path"]` answers {status, data} or {error}.
local function world(opts: { [string]: any }?)
	local options = opts or {}
	local json = support.json()
	local timers = support.timers()
	local kv = support.kv(options.kv)
	local requests: { { method: string, path: string, body: any, auth: string? } } = {}
	local secrets_store: { [string]: string } = options.secrets_store or {}
	local secrets = if options.no_secrets then nil else {
		get = function(name: string)
			if options.keychain_hangs then
				return support.awaitable(nil, nil, true)
			end
			return support.awaitable(secrets_store[name], options.keychain_locked)
		end,
		set = function(name: string, value: string?)
			if options.keychain_refuses then
				return support.awaitable(nil, "no secret service")
			end
			secrets_store[name] = value :: any
			return support.awaitable(nil, nil)
		end,
	}
	local routes: { [string]: any } = options.routes or {}
	local w = { json = json, timers = timers, kv = kv, requests = requests, secrets = secrets_store, routes = routes, notices = {} }
	local files: { [string]: string } = {
		[ENDPOINT] = json.encode({ origin = ORIGIN, pairing = "/api/ingress/pair" }),
	}
	if options.offline then
		files = {}
	end
	w.client = Client.new({
		fetch = function(url: string, request: { [string]: any }, cb)
			local path = string.sub(url, #ORIGIN + 1)
			local auth = request.headers.Authorization
			local body = if request.body then json.decode(request.body) else nil
			table.insert(requests, { method = request.method, path = path, body = body, auth = auth })
			local answer = routes[request.method .. " " .. path]
			if type(answer) == "function" then
				answer = answer(body, auth)
			end
			if answer == nil or answer.error then
				cb({ status = 0, headers = {}, body = "", error = if answer then answer.error else "connection refused", timed_out = false }, "cx")
			else
				cb({ status = answer.status, headers = {}, body = if answer.data ~= nil then json.encode(answer.data) else "" }, "cx")
			end
		end,
		json = json,
		kv = kv,
		secrets = secrets,
		read_file = function(path: string, _max: number): string
			local text = files[path]
			if not text then
				error("tern.fs.read " .. path .. ": No such file or directory (os error 2)")
			end
			return text
		end,
		timer = timers.timer,
		now = function()
			return (options.clock or { 0 })[1]
		end,
		plugin_dir = options.plugin_dir or DIR,
	})
	w.client.on_pairing = function(_cx, code, seconds)
		table.insert(w.notices, "pairing " .. code .. " " .. seconds)
	end
	w.client.on_paired = function(_cx)
		table.insert(w.notices, "paired")
	end
	w.client.on_notice = function(_cx, level, text, _sub)
		table.insert(w.notices, level .. " " .. text)
	end
	return w
end

local function pairing_routes(routes: { [string]: any }, token: string, answers: { string }?)
	local queue = answers or { "pending", "approved" }
	routes["POST /api/ingress/pair"] = { status = 202, data = { status = "pending", request = "secret", code = "ABC-DEF", expires_in = 300 } }
	routes["POST /api/ingress/pair/status"] = function(body)
		assert(body.request == "secret")
		local status = table.remove(queue, 1) or "pending"
		return { status = 200, data = if status == "approved" then { status = status, token = token, client = {} } else { status = status } }
	end
end

local function recorder()
	local results: { any } = {}
	return results, function(outcome, _cx)
		table.insert(results, outcome)
	end
end

local function paths(w): { string }
	local out = {}
	for _, request in w.requests do
		table.insert(out, request.method .. " " .. request.path)
	end
	return out
end

return function(t: any)
	t.test("says rika! isn't running when there is no endpoint file", function()
		local w = world({ offline = true })
		local results, done = recorder()
		w.client:request("cx", "GET", "/api/ingress/recent", nil, done)
		t.eq(results[1].kind, "offline")
		t.has(results[1].message, "isn't running")
		t.eq(#w.requests, 0)
	end)

	t.test("refuses a copy rika! did not install", function()
		local w = world({ plugin_dir = "/src/kitty/integrations/tern" })
		local results, done = recorder()
		w.client:request("cx", "GET", "/api/ingress/recent", nil, done)
		t.eq(results[1].kind, "unconfigured")
	end)

	t.test("pairs on first use, keeps the token in the keychain and sends the request", function()
		local w = world()
		pairing_routes(w.routes, "kitty_new")
		w.routes["POST /api/ingress/inbox"] = function(_, auth)
			return if auth == "Bearer kitty_new" then { status = 200, data = { page = 2 } } else { status = 401 }
		end
		local results, done = recorder()
		w.client:request("cx", "POST", "/api/ingress/inbox", { blocks = { { type = "text", text = "hi" } } }, done)
		t.eq(w.requests[1].body, { name = "Tern", kind = "tern-plugin", scopes = { "capture", "read" } })
		t.eq(w.requests[1].auth, nil, "pairing carries no token")
		t.eq(w.notices, { "pairing ABC-DEF 300" })
		t.eq(#results, 0, "waits for approval")
		w.timers.flush()
		t.eq(paths(w), { "POST /api/ingress/pair", "POST /api/ingress/pair/status", "POST /api/ingress/pair/status", "POST /api/ingress/inbox" })
		t.eq(results[1].kind, "ok")
		t.eq(results[1].data, { page = 2 })
		t.eq(w.secrets.token, "kitty_new")
		t.eq(w.kv.data.token, nil, "never in the plain file")
		t.eq(w.kv.data.token_store, "keychain")
		t.eq(w.kv.data.workspace, "/home/u/ws/.private")
		t.eq(w.notices[2], "paired")
	end)

	t.test("reuses the stored token without pairing again", function()
		local w = world({ secrets_store = { token = "kitty_old" }, kv = { workspace = "/home/u/ws/.private" } })
		w.routes["GET /api/ingress/recent"] = { status = 200, data = { pages = {} } }
		local results, done = recorder()
		w.client:request("cx", "GET", "/api/ingress/recent", nil, done)
		t.eq(paths(w), { "GET /api/ingress/recent" })
		t.eq(w.requests[1].auth, "Bearer kitty_old")
		t.eq(results[1].kind, "ok")
	end)

	t.test("pairs again when the token belongs to another workspace", function()
		local w = world({ secrets_store = { token = "kitty_other" }, kv = { workspace = "/elsewhere/.private" } })
		pairing_routes(w.routes, "kitty_here", { "approved" })
		w.routes["GET /api/ingress/client"] = { status = 200, data = { scopes = { "capture" } } }
		local results, done = recorder()
		w.client:request("cx", "GET", "/api/ingress/client", nil, done)
		w.timers.flush()
		t.eq(w.requests[#w.requests].auth, "Bearer kitty_here")
		t.eq(results[1].kind, "ok")
	end)

	t.test("pairs again after rika! revoked the token, then retries once", function()
		local w = world({ secrets_store = { token = "kitty_revoked" }, kv = { workspace = "/home/u/ws/.private" } })
		pairing_routes(w.routes, "kitty_fresh", { "approved" })
		w.routes["POST /api/ingress/notes"] = function(_, auth)
			return if auth == "Bearer kitty_fresh" then { status = 200, data = { id = 5 } } else { status = 401, data = { error = "A paired client token is required" } }
		end
		local results, done = recorder()
		w.client:request("cx", "POST", "/api/ingress/notes", { text = "x" }, done)
		w.timers.flush()
		t.eq(paths(w), { "POST /api/ingress/notes", "POST /api/ingress/pair", "POST /api/ingress/pair/status", "POST /api/ingress/notes" })
		t.eq(results[1].kind, "ok")
		t.eq(w.secrets.token, "kitty_fresh")
	end)

	t.test("stops after one re-pairing that rika! still refuses", function()
		local w = world({ secrets_store = { token = "kitty_revoked" }, kv = { workspace = "/home/u/ws/.private" } })
		pairing_routes(w.routes, "kitty_fresh", { "approved" })
		w.routes["POST /api/ingress/notes"] = { status = 401 }
		local results, done = recorder()
		w.client:request("cx", "POST", "/api/ingress/notes", { text = "x" }, done)
		w.timers.flush()
		t.eq(#results, 1)
		t.eq(results[1].kind, "unauthorized")
	end)

	t.test("reports a denied or expired pairing", function()
		local w = world()
		pairing_routes(w.routes, "never", { "denied" })
		local results, done = recorder()
		w.client:request("cx", "GET", "/api/ingress/recent", nil, done)
		w.timers.flush()
		t.eq(results[1].kind, "denied")
		t.eq(w.secrets.token, nil)

		local clock = { 0 }
		local late = world({ clock = clock })
		pairing_routes(late.routes, "never", { "pending", "pending" })
		local expired, finish = recorder()
		late.client:request("cx", "GET", "/api/ingress/recent", nil, finish)
		late.timers.run()
		clock[1] = 301 * 1000
		late.timers.flush()
		t.eq(expired[1].kind, "expired")
	end)

	t.test("keeps polling through a brief outage while the request waits", function()
		local w = world()
		pairing_routes(w.routes, "kitty_new", { "pending" })
		local status = w.routes["POST /api/ingress/pair/status"]
		local calls = 0
		w.routes["POST /api/ingress/pair/status"] = function(body)
			calls += 1
			if calls == 2 then
				return { error = "connection refused" }
			end
			return status(body)
		end
		w.routes["GET /api/ingress/recent"] = { status = 200, data = {} }
		local results, done = recorder()
		w.client:request("cx", "GET", "/api/ingress/recent", nil, done)
		for _ = 1, 3 do
			w.timers.run()
		end
		t.eq(#results, 0)
		w.routes["POST /api/ingress/pair/status"] = { status = 200, data = { status = "approved", token = "kitty_new" } }
		w.timers.flush()
		t.eq(results[1].kind, "ok")
	end)

	t.test("reports the build switch that turned the plugin off", function()
		local w = world()
		w.routes["POST /api/ingress/pair"] = { status = 403, data = { error = "The kitty Tern plugin is turned off in this rika!'s build (Settings > Build)" } }
		local results, done = recorder()
		w.client:request("cx", "GET", "/api/ingress/recent", nil, done)
		t.eq(results[1].kind, "disabled")
		t.has(results[1].message, "turned off")
	end)

	t.test("shares one pairing between requests made while it waits", function()
		local w = world()
		pairing_routes(w.routes, "kitty_new")
		w.routes["GET /api/ingress/recent"] = { status = 200, data = {} }
		local results, done = recorder()
		w.client:request("cx", "GET", "/api/ingress/recent", nil, done)
		w.client:request("cx", "GET", "/api/ingress/recent", nil, done)
		w.timers.flush()
		local pairs = 0
		for _, path in paths(w) do
			if path == "POST /api/ingress/pair" then
				pairs += 1
			end
		end
		t.eq(pairs, 1)
		t.eq(#results, 2)
		t.eq(w.notices[2], "pairing ABC-DEF 300", "the code is shown again")
	end)

	t.test("falls back to the plugin data file only without a working keychain", function()
		local bare = world({ no_secrets = true })
		pairing_routes(bare.routes, "kitty_plain", { "approved" })
		bare.routes["GET /api/ingress/recent"] = { status = 200, data = {} }
		local _, done = recorder()
		bare.client:request("cx", "GET", "/api/ingress/recent", nil, done)
		bare.timers.flush()
		t.eq(bare.kv.data.token, "kitty_plain")
		t.eq(bare.kv.data.token_store, "kv")
		t.eq(bare.notices, { "pairing ABC-DEF 300", "info rika! token kept in Tern's plugin data", "paired" })

		local refused = world({ keychain_refuses = true })
		pairing_routes(refused.routes, "kitty_plain", { "approved" })
		refused.routes["GET /api/ingress/recent"] = { status = 200, data = {} }
		refused.client:request("cx", "GET", "/api/ingress/recent", nil, done)
		refused.timers.flush()
		t.eq(refused.kv.data.token, "kitty_plain")
	end)

	t.test("does not pair again while the keychain is locked or silent", function()
		local locked = world({ secrets_store = { token = "kitty_x" }, kv = { workspace = "/home/u/ws/.private" }, keychain_locked = "locked" })
		local results, done = recorder()
		locked.client:request("cx", "GET", "/api/ingress/recent", nil, done)
		t.eq(results[1].kind, "keychain")
		t.eq(#locked.requests, 0)

		local silent = world({ kv = { workspace = "/home/u/ws/.private" }, keychain_hangs = true })
		local waited, finish = recorder()
		silent.client:request("cx", "GET", "/api/ingress/recent", nil, finish)
		t.eq(#waited, 0)
		silent.timers.flush()
		t.eq(waited[1].kind, "keychain")
		t.eq(#silent.requests, 0)
	end)
end
