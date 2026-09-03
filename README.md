# Dossier INSEE

Permet d'interroger et de créer le dossier complet INSEE pour les territoires choisis.

## Assistant IA : OpenRouter

L'assistant conversationnel s'appuie sur [OpenRouter](https://openrouter.ai), ce qui permet
de choisir librement le LLM (Anthropic, Google, OpenAI, Mistral, Meta, modèles gratuits…)
depuis le panneau latéral de l'application.

### Configuration

Renseignez la clé dans les secrets Streamlit (`Settings > Secrets` sur Streamlit Cloud,
ou `.streamlit/secrets.toml` en local — voir `.streamlit/secrets.toml.example`) :

```toml
OPENROUTER_API_KEY = "sk-or-v1-..."
OPENROUTER_MODEL   = "google/gemini-3.7-flash"  # optionnel : modèle par défaut
```

Les variables d'environnement (fichier `.env`) sont acceptées en repli si aucun secret
Streamlit n'est défini.

### Vérification locale

Les contrôles statiques du projet se lancent sans dépendance externe :

```bash
npm test
```

### Choix du modèle

Dans la barre latérale, l'expander **🤖 Modèle IA (OpenRouter)** permet de :

- parcourir le catalogue OpenRouter récupéré en direct (mis en cache 1 h),
- filtrer par nom ou n'afficher que les modèles gratuits,
- saisir un identifiant personnalisé (`fournisseur/modèle`) prioritaire sur la liste,
- régler la température.

Si le catalogue n'est pas joignable, une liste de secours est proposée.

### Fiabilité des réponses

- Les réponses sont **streamées** (`stream: true`) : la connexion vers le fournisseur ne reste
  jamais silencieuse, ce qui évite les abandons amont (`The operation was aborted`) sur les
  réponses longues. Une relance automatique est tentée tant qu'aucun texte n'a été affiché.
- La longueur est bornée (`MAX_TOKENS`) et les erreurs HTTP (401, 402, 429…) sont traduites
  en messages actionnables.
- Le contexte envoyé au modèle inclut la **répartition de la population par âge et par sexe**
  issue du recensement (API Melodi de l'INSEE). Si cette donnée n'est pas disponible pour le
  territoire, le modèle est explicitement instruit de le dire plutôt que de l'estimer.
