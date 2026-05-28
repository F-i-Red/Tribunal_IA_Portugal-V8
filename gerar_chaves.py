#!/usr/bin/env python3
"""
gerar_chaves.py — Gera chaves seguras para o .env em produção/GOV_MODE.
Uso: python gerar_chaves.py
"""
import secrets
from cryptography.fernet import Fernet

print("=" * 60)
print("TRIBUNAL IA PORTUGAL V7 — Gerador de Chaves Seguras")
print("=" * 60)
print()
print("Copia estes valores para o teu .env:\n")

api_key = secrets.token_hex(32)
print(f"API_SECRET_KEY={api_key}")

fernet_key = Fernet.generate_key().decode()
print(f"AUDIT_ENCRYPTION_KEY={fernet_key}")

print()
print("=" * 60)
print("⚠️  GUARDA ESTAS CHAVES EM LOCAL SEGURO.")
print("    Não as commites para o repositório.")
print("    Em produção, usa um gestor de secrets (Vault, AWS Secrets Manager, etc.)")
print("=" * 60)
