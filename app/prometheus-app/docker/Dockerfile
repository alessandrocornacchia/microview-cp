FROM python:3.11.11-slim

WORKDIR /app

COPY . /app

RUN pip install prometheus_client

EXPOSE 8000

# Entry point script
RUN chmod +x ./entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
