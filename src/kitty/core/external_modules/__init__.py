"""External modules: add-ons installed from a GitHub release (docs/MODULES.md).

* ``manifest``  - ``rika-module.json`` (api 1) validation;
* ``github``    - the latest release, its checksum and a verified download;
* ``archive``   - extraction that cannot escape its folder;
* ``registry``  - the pinned installs (table ``external_modules``) and their folders;
* ``installer`` - install/update and update checks, tying the above together.

Loading an installed module into the running website (Python import, process start,
routes, views) is kitty.web_external_modules. Like built-in modules, an install, update
or removal takes effect when rika! next starts.
"""
