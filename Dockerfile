FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    iperf \
    iproute2 \
    iptables \
    # For debug
    iputils-ping \
    net-tools \
    && rm -rf /var/lib/apt/lists/* \
    && pip install tcconfig==0.29.1

RUN pip3 install Flask

WORKDIR /app

COPY . /app

ENTRYPOINT ["python3", "main.py", "--debug"]
