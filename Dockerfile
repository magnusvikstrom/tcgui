FROM python:3.8-slim-buster

RUN apt-get update && apt-get install -y --no-install-recommends \
    iperf \
    iproute2 \
    iptables \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/* \
    && pip install tcconfig==0.26.0

RUN pip3 install Flask

WORKDIR /app

COPY . /app

ENTRYPOINT ["python3", "main.py", "--debug"]
