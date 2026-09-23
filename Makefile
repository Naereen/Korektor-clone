# Makefile de Korektor
# ====================
#
# Korektor est une application Flask d'aide à la correction de copies
# manuscrites avec des modèles de langage (LLM).
#
# Prerequis :
#   - Python 3.12 ou plus récent
#   - uv, installé et disponible dans le PATH
#
# Utilisation :
#   make help       Afficher les commandes disponibles
#   make run        Lancer l'application sur http://127.0.0.1:5000
#   make test       Exécuter la suite de tests
#   make compile    Vérifier la syntaxe Python
#   make check      Compiler puis exécuter les tests
#   make coverage   Lancer les tests et afficher la couverture de code
#   make format     Formater le code Python
#   make format-check  Vérifier le formatage sans modifier les fichiers
#
# Les dépendances sont déclarées dans pyproject.toml et gérées par uv.

.DEFAULT_GOAL := help

PYTHON := uv run python
PYTEST := uv run pytest

.PHONY: help run test coverage compile check format format-check

help: ## Affiche les commandes disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-14s\033[0m %s\n", $$1, $$2}'

run: ## Lance l'application Flask
	$(PYTHON) correct_ai/app.py

test: ## Lance tous les tests
	$(PYTEST) tests

coverage: ## Lance les tests et affiche la couverture de code
	uv run --with coverage coverage run -m pytest tests -q
	uv run --with coverage coverage report -m

compile: ## Compile les sources et les tests pour vérifier leur syntaxe
	$(PYTHON) -m compileall -q correct_ai tests

check: compile test ## Compile puis lance les tests

format: ## Formate le code Python
	uv run isort correct_ai tests
	uv run black correct_ai tests

format-check: ## Vérifie le formatage sans modifier les fichiers
	uv run isort --check-only correct_ai tests
	uv run black --check correct_ai tests
