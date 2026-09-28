# Worker OAuth Stellantis — le navigateur qui va chercher le code d'autorisation.
#
# Stellantis ne delivre le code OAuth qu'au terme d'un vrai parcours de login
# web, et le redirige vers une URL « mymX:// » que seule l'application mobile
# sait recevoir. Ce service pilote un Chromium headless qui joue ce parcours et
# intercepte le code au vol. L'integration stellantis_vehicles l'appelle
# UNIQUEMENT lors d'une configuration ou d'une re-authentification.
#
# Il existe une instance publique sur Render, proposee par defaut par
# l'integration. Elle recoit l'email et le mot de passe MyPeugeot EN CLAIR :
# c'est la raison d'etre de cette copie locale, le reveil du plan gratuit
# (~50 s) n'etant qu'un desagrement secondaire.
#
# Le Dockerfile amont installe les bibliotheques Chromium a la main, une liste
# qui ne resout plus sur Debian recent (libasound2 y est devenu libasound2t64).
# On epingle donc bookworm et on delegue la liste a Playwright lui-meme, qui
# connait ses propres dependances.
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Versions alignees sur le requirements.txt amont.
RUN pip install --no-cache-dir \
        fastapi==0.110.0 \
        "uvicorn[standard]==0.29.0" \
        playwright==1.42.0 \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Le code du worker vient de l'amont, epingle sur un commit : pas de fork a
# maintenir, et une reconstruction donne le meme resultat qu'aujourd'hui.
ADD https://raw.githubusercontent.com/andreadegiovine/homeassistant-stellantis-vehicles-worker-v2/63c4fab139074087697b56aa310647c58dc51ad7/main.py /app/main.py
RUN chmod 644 /app/main.py

EXPOSE 3000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000"]
