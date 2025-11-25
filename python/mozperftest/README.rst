===========
mozperftest
===========

**mozperftest** can be used to run performance tests against browsers.
See the docs directory.

ML services proxy
-----------------
Enable the ML services proxy layer with ``--ml-services-proxy`` to route
Remote Settings traffic through a local endpoint. Use
``--ml-services-proxy-upstream`` to pick the origin (defaults to production),
``--ml-services-proxy-route`` for extra prefix→upstream mappings, and
``--ml-services-proxy-fixture`` to serve files from disk. The layer sets
``services.settings.server`` to the proxy URL and exports
``MOZ_REMOTE_SETTINGS_DEVTOOLS=1`` for override support.

The ``eval-mochitest`` flavor enables the ML services proxy by default and
runs mochitest with eval-focused defaults.
