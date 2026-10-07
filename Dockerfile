FROM alpine:3.20
RUN apk add --no-cache ffmpeg python3 curl
COPY relay.py /relay.py
COPY logos /logos
EXPOSE 8765
CMD ["python3","-u","/relay.py"]
