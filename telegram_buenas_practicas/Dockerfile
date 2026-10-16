FROM ghcr.io/home-assistant/base:3.24

RUN apk add --no-cache python3 ca-certificates

COPY run.sh bot.py /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
