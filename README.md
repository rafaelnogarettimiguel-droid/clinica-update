# ClinicaUpdate

Curadoria semanal automática de artigos em medicina interna via PubMed.

Gerado toda segunda-feira às 3h (Brasília) pelo GitHub Actions.

## Estrutura

```
clinica-update/
├── generate.py              # script principal
├── .github/
│   └── workflows/
│       └── weekly.yml       # agendamento automático
└── docs/                    # gerado automaticamente
    ├── index.html           # edição mais recente
    ├── editions.json        # manifest de todas as edições
    ├── 2026-05-12.html
    ├── 2026-05-19.html
    └── ...
```

## Como rodar manualmente

```bash
python generate.py
```

Os arquivos são gerados na pasta `docs/`.
