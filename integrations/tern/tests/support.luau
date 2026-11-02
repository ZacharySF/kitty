--!nonstrict
-- A tiny test harness and fakes of the Tern API for the plugin's pure modules.
-- Run every test with `luau tests/run.luau` from the plugin directory.

local support = {}

local failures: { string } = {}
local passed = 0

local function show(value: any): string
	if type(value) == "string" then
		return string.format("%q", value)
	elseif type(value) ~= "table" then
		return tostring(value)
	end
	local keys = {}
	for key in value do
		table.insert(keys, key)
	end
	table.sort(keys, function(a, b)
		return tostring(a) < tostring(b)
	end)
	local parts = {}
	for _, key in keys do
		table.insert(parts, tostring(key) .. "=" .. show(value[key]))
	end
	return "{" .. table.concat(parts, ", ") .. "}"
end
support.show = show

local function same(a: any, b: any): boolean
	if type(a) ~= "table" or type(b) ~= "table" then
		return a == b
	end
	for key, value in a do
		if not same(value, b[key]) then
			return false
		end
	end
	for key in b do
		if a[key] == nil then
			return false
		end
	end
	return true
end

function support.eq(actual: any, expected: any, label: string?)
	if not same(actual, expected) then
		error(string.format("%s: expected %s, got %s", label or "eq", show(expected), show(actual)), 2)
	end
end

function support.ok(value: any, label: string?)
	if not value then
		error((label or "ok") .. ": expected a true value, got " .. show(value), 2)
	end
end

function support.has(text: any, part: string, label: string?)
	if type(text) ~= "string" or not string.find(text, part, 1, true) then
		error(string.format("%s: expected %s to contain %q", label or "has", show(text), part), 2)
	end
end

function support.test(name: string, fn: () -> ())
	local ok, err = pcall(fn)
	if ok then
		passed += 1
	else
		table.insert(failures, name .. ": " .. tostring(err))
	end
end

function support.finish(): boolean
	for _, failure in failures do
		print("FAIL " .. failure)
	end
	print(string.format("%d passed, %d failed", passed, #failures))
	return #failures == 0
end

-- Fakes -------------------------------------------------------------------------------

--- JSON stand-in: `encode` files the value away and returns a handle `decode` resolves.
function support.json()
	local stored: { [string]: any } = {}
	local count = 0
	return {
		encode = function(value: any): string
			count += 1
			local handle = "json#" .. count
			stored[handle] = value
			return handle
		end,
		decode = function(text: string): any
			if stored[text] == nil then
				error("not JSON: " .. text)
			end
			return stored[text]
		end,
	}
end

--- Timers that run only when the test calls `run()` (oldest first) or `flush()`.
function support.timers()
	local queue: { { ms: number, fn: (any) -> (), cancelled: boolean } } = {}
	local timers = {}
	function timers.timer(ms: number, fn: (any) -> ())
		local entry = { ms = ms, fn = fn, cancelled = false }
		table.insert(queue, entry)
		return {
			cancel = function(_self)
				entry.cancelled = true
			end,
		}
	end
	function timers.run(): boolean
		local entry = table.remove(queue, 1)
		if not entry then
			return false
		end
		if not entry.cancelled then
			entry.fn("cx")
		end
		return true
	end
	function timers.flush(limit: number?)
		local steps = 0
		while timers.run() do
			steps += 1
			assert(steps < (limit or 100), "timers never settled")
		end
	end
	function timers.pending(): number
		local count = 0
		for _, entry in queue do
			if not entry.cancelled then
				count += 1
			end
		end
		return count
	end
	return timers
end

--- An awaitable resolved now (`value`, `err`), or never when `hang` is set.
function support.awaitable(value: any, err: string?, hang: boolean?)
	return {
		next = function(_self, fn)
			if not hang then
				fn(value, err, "cx")
			end
		end,
	}
end

--- A key/value store like tern.kv.
function support.kv(initial: { [string]: any }?)
	local data: { [string]: any } = table.clone(initial or {})
	return {
		data = data,
		get = function(key: string): any
			return data[key]
		end,
		set = function(key: string, value: any)
			data[key] = value
		end,
	}
end

return support
