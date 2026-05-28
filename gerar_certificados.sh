#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# gerar_certificados.sh
# Gera certificados TLS auto-assinados para desenvolvimento local.
# Em produção, usa certificados de uma CA oficial (SCEE ou Let's Encrypt).
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

CERTS_DIR="nginx/certs"
mkdir -p "$CERTS_DIR"

echo "▶ A gerar certificados TLS para desenvolvimento..."

# CA raiz (apenas para dev)
openssl req -x509 -newkey rsa:4096 -days 365 -nodes \
  -keyout "$CERTS_DIR/ca.key" \
  -out "$CERTS_DIR/ca.crt" \
  -subj "/C=PT/ST=Lisboa/O=Tribunal IA Dev CA/CN=tribunal-ia-dev-ca"

# Certificado do servidor
openssl req -newkey rsa:4096 -nodes \
  -keyout "$CERTS_DIR/server.key" \
  -out "$CERTS_DIR/server.csr" \
  -subj "/C=PT/ST=Lisboa/O=Tribunal IA Portugal/CN=localhost"

openssl x509 -req -days 365 \
  -in "$CERTS_DIR/server.csr" \
  -CA "$CERTS_DIR/ca.crt" \
  -CAkey "$CERTS_DIR/ca.key" \
  -CAcreateserial \
  -out "$CERTS_DIR/server.crt" \
  -extfile <(printf "subjectAltName=DNS:localhost,DNS:tribunal-ia,IP:127.0.0.1")

# Certificado cliente para mTLS com Ollama
openssl req -newkey rsa:4096 -nodes \
  -keyout "$CERTS_DIR/client.key" \
  -out "$CERTS_DIR/client.csr" \
  -subj "/C=PT/ST=Lisboa/O=Tribunal IA Portugal/CN=tribunal-ia-client"

openssl x509 -req -days 365 \
  -in "$CERTS_DIR/client.csr" \
  -CA "$CERTS_DIR/ca.crt" \
  -CAkey "$CERTS_DIR/ca.key" \
  -CAcreateserial \
  -out "$CERTS_DIR/client.crt"

rm -f "$CERTS_DIR"/*.csr "$CERTS_DIR"/*.srl
chmod 600 "$CERTS_DIR"/*.key
chmod 644 "$CERTS_DIR"/*.crt

echo ""
echo "✅ Certificados gerados em $CERTS_DIR/"
echo "   server.crt / server.key  — Nginx TLS"
echo "   client.crt / client.key  — mTLS Ollama"
echo "   ca.crt                   — CA de desenvolvimento"
echo ""
echo "⚠️  Estes certificados são APENAS para desenvolvimento local."
echo "    Em produção usa certificados da SCEE (infra.gov.pt) ou Let's Encrypt."
