# IND2616-ProjetoMMGD

"Como a micro e minigeração distribuída está se difundindo no Brasil, qual foi o efeito da Lei 14.300 nessa trajetória, e até onde ela chega até 2030 em cada região?"

## Como rodar o projeto

### 1. Clonar o repositório

```sh
git clone https://github.com/BrunoWolf03/IND2616-ProjetoMMGD.git
cd IND2616-ProjetoMMGD
```

### 2. Criar e ativar o ambiente virtual

Linux / macOS:

```sh
python3 -m venv .venv
source .venv/bin/activate
```

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Instalar as dependências

```sh
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Baixar os dados

Os dados **não** são versionados no repositório (a pasta `data/` está no `.gitignore`). Um único comando baixa e prepara tudo:

```sh
python src/main.py                      # tudo (~10 min na primeira vez, ~1,5 GB)
python src/main.py --etapas ons inmet   # só algumas etapas
python src/main.py --refresh            # baixa de novo ONS e ANEEL (retratos mais recentes)
```

Todas as etapas usam cache: rodar de novo só baixa o que falta.

| Etapa | Script | Fonte | Saída principal |
|---|---|---|---|
| `ons` | `src/extract_ons.py` | API de carga verificada do ONS (SP, MG, RJ, ES) | `data/processed/ons_carga_horaria.parquet` |
| `inmet` | `src/extract_inmet.py` | estações automáticas do INMET (Sudeste) | `data/processed/inmet_horario_por_uf.parquet` |
| `aneel` | `src/extract_aneel.py` | cadastro de MMGD da ANEEL (dados abertos) | `data/raw/aneel/*.parquet` |
| `preproc` | `src/preprocess_aneel.py` | base da ANEEL | `data/interim/aneel_gd_preproc.parquet` |

Cada script também roda sozinho (`python src/extract_ons.py --help`). As checagens de qualidade são impressas no terminal e salvas em `data/interim/checks_*`; os gráficos vão para `resultados/`.

### 5. Rodar os notebooks

Abra o `analiseExploratoria.ipynb` no VSCode (ou Jupyter) e selecione o kernel do `.venv` (`.venv/bin/python`).

> Se instalar algum pacote novo, atualize o `requirements.txt` com `pip freeze > requirements.txt`.
