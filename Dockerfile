FROM python:3.11-slim AS builder

# Install build dependencies for liboqs
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake gcc g++ ninja-build git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Build liboqs with shared libraries
RUN git clone --depth 1 --branch 0.12.0 https://github.com/open-quantum-safe/liboqs.git /tmp/liboqs \
    && cd /tmp/liboqs && mkdir build && cd build \
    && cmake -GNinja -DBUILD_SHARED_LIBS=ON -DCMAKE_INSTALL_PREFIX=/opt/oqs .. \
    && ninja && ninja install \
    && rm -rf /tmp/liboqs

# Runtime image
FROM python:3.11-slim

COPY --from=builder /opt/oqs /opt/oqs
ENV LD_LIBRARY_PATH=/opt/oqs/lib

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir liboqs-python cryptography aiohttp

COPY qbit_network/ qbit_network/
COPY cli/ cli/
COPY run_node.py .

# Default data directory
ENV QBIT_DATA_DIR=/data
VOLUME /data

EXPOSE 9000 8545

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8545/api/v1/health')" || exit 1

ENTRYPOINT ["python3", "run_node.py"]
CMD ["--data-dir", "/data"]
