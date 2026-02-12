const axios = require('axios');
const querystring = require('querystring');

// --- CONFIGURATION ---
const API_BASE_URL = 'https://api.insee.fr/api-sirene/3.11';
const API_KEY = process.env.INSEE_API_KEY; // Ceci est le jeton d'accès direct

// --- VALIDATION ---
if (!API_KEY) {
  console.error('\x1b[31mErreur : La clé d\'API est manquante.\x1b[0m');
  console.error('Veuillez définir la variable d\'environnement INSEE_API_KEY.');
  console.error('Exemple: export INSEE_API_KEY="votre_jeton_ici"');
  process.exit(1);
}

const searchTerm = process.argv[2];
if (!searchTerm) {
  console.error('\x1b[31mErreur : Veuillez fournir le nom de la collectivité en argument.\x1b[0m');
  console.error('Exemple: node index.js "Mairie de Toulouse"');
  process.exit(1);
}

// --- LOGIQUE PRINCIPALE ---
const findCollectivite = async () => {
  try {
    const apiClient = axios.create({
      baseURL: API_BASE_URL,
      headers: {
        'Authorization': `Bearer ${API_KEY}`, // Utilisation du jeton direct
        'Accept': 'application/json'
      }
    });

    // 1. Construire la requête de recherche par nom uniquement
    const query = `raisonSociale:${querystring.escape(searchTerm)}`;
    
    console.log(`Recherche de la collectivité "${searchTerm}"...`);

    // 2. Construire l'URL manuellement pour éviter l'encodage du ':' par axios
    const searchUrl = `/siren?q=${query}&nombre=1`; // On prend le premier résultat
    
    console.log(`URL de recherche: ${searchUrl}`);

    const searchResponse = await apiClient.get(searchUrl);

    if (!searchResponse.data.unitesLegales || searchResponse.data.unitesLegales.length === 0) {
      console.log(`\x1b[33mAucune collectivité trouvée pour le nom "${searchTerm}".\x1b[0m`);
      console.log('Essayez avec une dénomination plus officielle ou plus simple (ex: "Commune de Paris").');
      return;
    }

    // 3. Prendre le premier résultat directement, sans filtrage côté client
    const siren = searchResponse.data.unitesLegales[0].siren;
    console.log(`\x1b[32mSIREN trouvé : ${siren}\x1b[0m. Récupération du dossier complet...`);

    const dossierResponse = await apiClient.get(`/siren/${siren}`);

    console.log('\n\x1b[1m--- Dossier complet de l\'unité légale ---\x1b[0m');
    console.log(JSON.stringify(dossierResponse.data, null, 2));

  } catch (error) {
    console.error('\x1b[31m--- Une erreur est survenue ---\x1b[0m');
    if (error.response) {
        console.error(`Erreur de l\'API : ${error.response.status} - ${error.response.statusText}`);
        console.error('Détails:', JSON.stringify(error.response.data, null, 2));
    } else if (error.request) {
      console.error('Erreur de réseau : Impossible de contacter l\'API de l\'INSEE.');
    } else {
      console.error('Erreur inattendue :', error.message);
    }
  }
};

findCollectivite();