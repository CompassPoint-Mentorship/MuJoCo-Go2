#!/usr/bin/env bash
# Generate a self-signed TLS certificate for the student labs.
#
#   ./make-cert.sh                      # every address this host answers on
#   ./make-cert.sh go2.lan 10.0.0.5     # plus extra names/addresses
#
# WHY: browsers only allow getUserMedia (the microphone, for recording a
# student's voice) on a "secure context" -- https, or localhost. A self-signed
# certificate is enough: once the student clicks through the warning the origin
# is https, isSecureContext is true, and recording works.
#
# The cost is that one-time warning per student per browser. The alternative
# (a real certificate) needs a public domain and DNS, which is a lot of setup
# for a machine that only serves the local network.
set -euo pipefail
cd "$(dirname "$0")"

CERT_DIR="${CERT_DIR:-$PWD/tls}"
DAYS="${DAYS:-825}"
mkdir -p "$CERT_DIR"

# Browsers ignore CN and require subjectAltName, and an IP must appear as an
# IP: entry -- a DNS: entry will not match https://192.168.1.215.
alts=("DNS:localhost" "IP:127.0.0.1")
add_ip()   { case " ${alts[*]} " in *" IP:$1 "*) ;; *) alts+=("IP:$1");; esac; }
add_name() { case " ${alts[*]} " in *" DNS:$1 "*) ;; *) alts+=("DNS:$1");; esac; }

if command -v tailscale >/dev/null 2>&1; then
  ts="$(tailscale ip -4 2>/dev/null | head -1 || true)"
  [ -n "$ts" ] && add_ip "$ts"
fi
while read -r ip; do [ -n "$ip" ] && add_ip "$ip"; done < <(
  ip -4 -o addr show scope global 2>/dev/null \
    | awk '$2 !~ /^(docker|br-|veth)/ {split($4,a,"/"); print a[1]}'
)
add_name "$(hostname)"
add_name "$(hostname).local"
for extra in "$@"; do
  if [[ "$extra" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then add_ip "$extra"; else add_name "$extra"; fi
done

SAN="$(IFS=,; echo "${alts[*]}")"
echo "Subject alternative names:"
printf '  %s\n' "${alts[@]}"

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$CERT_DIR/key.pem" -out "$CERT_DIR/cert.pem" \
  -days "$DAYS" -subj "/CN=go2-lab" \
  -addext "subjectAltName=$SAN" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth" 2>/dev/null

# The container runs as uid 1000 and must read these.
chmod 644 "$CERT_DIR/cert.pem"
chmod 640 "$CERT_DIR/key.pem"
chown "$(id -u):1000" "$CERT_DIR/key.pem" 2>/dev/null || chmod 644 "$CERT_DIR/key.pem"

echo
echo "Wrote $CERT_DIR/cert.pem and key.pem (valid $DAYS days)"
echo "Fingerprint (students can check this against the browser warning):"
openssl x509 -in "$CERT_DIR/cert.pem" -noout -fingerprint -sha256 | sed 's/^/  /'
echo
echo "Now restart the labs:  for f in students/*.env; do ./go2-student.sh up \"\$(basename \"\$f\" .env)\"; done"
