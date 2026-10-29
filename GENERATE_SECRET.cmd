@echo off
python -c "import secrets; print(secrets.token_urlsafe(32))"
pause
