# 🤖 Discord AI Bot — Setup Guide

## Funcionalidades
- 💬 Chat com AI (LLaMA 3.3 via Groq) com memória de conversa
- ✅ Checklist interativa com linguagem natural
- 📝 Gestão de tarefas com linguagem natural
- ⚡ Comandos slash: /perguntar, /checklist, /tarefas, /ajuda, /limpar_historico

## Deploy no Railway

### 1. Faz upload desta pasta para o GitHub
- Cria um repositório no GitHub (pode ser privado)
- Faz upload de todos os ficheiros

### 2. Railway
- Va a railway.app
- "New Project" → "Deploy from GitHub repo"
- Seleciona o teu repositório

### 3. Variáveis de ambiente (OBRIGATÓRIO)
No Railway, vai a "Variables" e adiciona:
```
DISCORD_TOKEN=o_teu_token_aqui
GROQ_API_KEY=a_tua_groq_key_aqui
```

### 4. Canais no Discord
Cria estes canais no teu servidor:
- `#checklist` — para listas interativas
- `#tarefas` — para gestão de tarefas
- `#bot` ou `#ia` — para chat livre com o bot

## Como usar

### Chat
- Menciona o bot: `@NomeDoBot qual a capital de França?`
- Em DM: escreve diretamente
- Nos canais #bot ou #ia: escreve livremente

### Checklist (#checklist)
- `adiciona comprar leite`
- `marca comprar leite como feito`
- `mostra a lista`
- `limpa os itens feitos`

### Tarefas (#tarefas)
- `adiciona tarefa reunião com cliente`
- `marca reunião como feita`
- `mostra as tarefas`
