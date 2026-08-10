"""MAOP performance / load test suite.

This package contains:

* ``k6_maop_load.js``        — k6 (JavaScript) HTTP load test for the MAOP API.
* ``locust_maop_load.py``    — Locust (Python) HTTP load test for the MAOP API.
* ``test_performance_smoke.py`` — pytest smoke tests that validate the load
  test scripts are syntactically correct and reference the expected
  endpoints, so CI catches regressions even when k6/Locust are not installed.

Run the load tests directly with:

    k6 run py/tests/performance/k6_maop_load.js
    locust -f py/tests/performance/locust_maop_load.py --host http://localhost:9079
"""