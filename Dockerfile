FROM thombashi/tcconfig

RUN pip3 install Flask

WORKDIR /app

COPY . /app

ENTRYPOINT ["python3", "main.py", "--debug"]
