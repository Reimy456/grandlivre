#!/usr/bin/env bash
# Regénération du code OpenAPI pour grandlivre.
#
# Référence : plan harmonic-hatching-trinket.md Phase 2 + verifications §4.
#
# Procédure :
#   1. Télécharge la spec brute depuis pennylane.readme.io/openapi/accounting.json.
#   2. Filtre aux paths v0.1 via scripts/filter_spec.py.
#   3. Génère le code Python via openapi-python-client 0.28.3.
#   4. Déplace le résultat dans src/grandlivre/_generated/.
#   5. Imprime la checksum SHA256 de la spec source pour traçabilité.
#
# Prérequis :
#   - venv activé avec openapi-python-client==0.28.3 installé.
#   - curl, jq optionnel (pour vérification HTTP).
#
# Usage :
#   bash scripts/regenerate_openapi.sh

set -euo pipefail

# Résolution des chemins absolus depuis l'emplacement du script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
GENERATED_DIR="${PROJECT_DIR}/src/grandlivre/_generated"

OPENAPI_URL="https://pennylane.readme.io/openapi/accounting.json"
RAW_SPEC="${PROJECT_DIR}/openapi_spec_raw.json"
FILTERED_SPEC="${PROJECT_DIR}/openapi_spec_filtered.json"
GEN_WORK_DIR="$(mktemp -d)"

echo "==> Téléchargement spec OpenAPI brute"
curl -fsSL "${OPENAPI_URL}" -o "${RAW_SPEC}"
RAW_BYTES=$(wc -c < "${RAW_SPEC}")
RAW_SHA=$(sha256sum "${RAW_SPEC}" | cut -d' ' -f1)
echo "    Source: ${OPENAPI_URL}"
echo "    Taille: ${RAW_BYTES} octets"
echo "    SHA256: ${RAW_SHA}"

echo "==> Filtrage paths v0.1"
python "${SCRIPT_DIR}/filter_spec.py" "${RAW_SPEC}" "${FILTERED_SPEC}"

echo "==> Génération openapi-python-client 0.28.3"
# Génération dans un dossier temporaire pour pouvoir inspecter avant déplacement.
cd "${GEN_WORK_DIR}"

# Configuration : nom du package généré = "pennylane_openapi", évite collision
# avec "grandlivre" qui est notre namespace principal.
cat > openapi_python_client_config.yml <<'EOF'
package_name_override: pennylane_openapi
project_name_override: pennylane-openapi-internal
use_path_prefixes_for_title_model_names: true
EOF

openapi-python-client generate \
    --path "${FILTERED_SPEC}" \
    --config openapi_python_client_config.yml \
    --overwrite || {
        echo "ERREUR génération openapi-python-client" >&2
        exit 1
    }

if [[ ! -d "${GEN_WORK_DIR}/pennylane-openapi-internal/pennylane_openapi" ]]; then
    echo "ERREUR : sortie attendue pennylane-openapi-internal/pennylane_openapi/ introuvable" >&2
    ls -la "${GEN_WORK_DIR}" >&2
    exit 1
fi

GEN_SOURCE="${GEN_WORK_DIR}/pennylane-openapi-internal/pennylane_openapi"

echo "==> Remplacement de ${GENERATED_DIR}"
# On préserve notre __init__.py d'override avec docstring,
# et on injecte le contenu généré dans un sous-dossier _gen_pkg/.
mkdir -p "${GENERATED_DIR}"
INIT_BACKUP="$(mktemp)"
cp "${GENERATED_DIR}/__init__.py" "${INIT_BACKUP}" 2>/dev/null || true

rm -rf "${GENERATED_DIR}"/*
mkdir -p "${GENERATED_DIR}"
cp -r "${GEN_SOURCE}"/* "${GENERATED_DIR}/"

# Restaurer notre __init__.py (avec note "ne pas éditer").
if [[ -s "${INIT_BACKUP}" ]]; then
    cp "${INIT_BACKUP}" "${GENERATED_DIR}/__init__.py"
else
    cat > "${GENERATED_DIR}/__init__.py" <<'EOF'
"""Output openapi-python-client 0.28.3, généré par scripts/regenerate_openapi.sh.

Ne pas éditer à la main. Toute modification doit passer par la regénération.
"""
EOF
fi

# Cleanup
rm -rf "${GEN_WORK_DIR}"

echo "==> Résumé"
echo "    Spec brute SHA256 : ${RAW_SHA}"
echo "    Fichiers générés  : $(find "${GENERATED_DIR}" -name '*.py' | wc -l)"
echo "    Path destination  : ${GENERATED_DIR}"
echo
echo "Pensez à committer ${GENERATED_DIR} (versionné dans git par décision 2026-05-11)."
