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

### 4. Adicionar os dados

Os dados **não** são versionados no repositório (a pasta `data/` está no `.gitignore`), então é preciso criar a pasta `data/` manualmente na raiz do projeto:

```sh
mkdir data
```

Depois, coloque os arquivos de dados dentro dela. A estrutura deve ficar assim:

```
IND2616-ProjetoMMGD/
├── data/
│   └── empreendimento-geracao-distribuida.parquet
├── analiseExploratoria.ipynb
├── requirements.txt
└── README.md
```

(Base de empreendimentos de geração distribuída da ANEEL — portal de dados abertos: https://dadosabertos.aneel.gov.br)

### 5. Rodar os notebooks

Abra o `analiseExploratoria.ipynb` no VSCode (ou Jupyter) e selecione o kernel do `.venv` (`.venv/bin/python`).

> Se instalar algum pacote novo, atualize o `requirements.txt` com `pip freeze > requirements.txt`.
