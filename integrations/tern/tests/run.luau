--!nonstrict
-- `luau tests/run.luau` from the plugin directory: the plugin's pure modules, without Tern.
local support = require("./support")

require("./core_test")(support)
require("./inbox_test")(support)
require("./client_test")(support)

if not support.finish() then
	error("plugin tests failed", 0)
end
