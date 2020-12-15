#!/bin/bash

echo "Testing uap"
    /bin/bash -c "source /.start_uap_python3_v1.0; uap --version; uap /opt/uap/example-configurations/test-download.yaml run-locally"
