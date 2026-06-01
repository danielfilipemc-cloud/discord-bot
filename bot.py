import discord
from discord.ext import commands
from discord import app_commands
import os
import json
import re
from groq import Groq
from datetime import datetime

# ─── CONFIG ───────────────────────────────────────────────────────────────────
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY")
GROQ_MODEL    = "llama-3.3-70b-versatile"

# IDs dos canais especiais (opcional — deixa 0 para desativar)
CHECKLIST_CHANNEL_NAME = "checklist"   # nome do canal de checklist
TASKS_CHANNEL_NAME     = "tarefas"     # nome do canal de tarefas

# ─── INICIALIZAÇÃO ────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
groq_client = Groq(api_key=GROQ_API_KEY)

# Histórico de conversa por utilizador (memória curta)
conversation_history = {}
MAX_HISTORY = 20  # últimas 20 mensagens por utilizador

# Dados de tarefas e checklists por servidor (em memória + ficheiro JSON)
DATA_FILE = "data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

server_data = load_data()

def get_server_data(guild_id):
    gid = str(guild_id)
    if gid not in server_data:
        server_data[gid] = {"tasks": [], "checklist": []}
    return server_data[gid]

# ─── GROQ: CHAT COM HISTÓRICO ─────────────────────────────────────────────────
def ask_groq(user_id, user_message, system_prompt=None):
    uid = str(user_id)
    if uid not in conversation_history:
        conversation_history[uid] = []

    conversation_history[uid].append({"role": "user", "content": user_message})

    # Manter histórico curto
    if len(conversation_history[uid]) > MAX_HISTORY:
        conversation_history[uid] = conversation_history[uid][-MAX_HISTORY:]

    sys_prompt = system_prompt or (
        "És um assistente inteligente e direto integrado no Discord. "
        "Respondes em português europeu. "
        "Quando te pedem para pesquisar algo, indica que não tens acesso à internet em tempo real "
        "mas respondes com o teu conhecimento atualizado até 2024. "
        "Sê conciso mas completo. Usa formatação Discord (** para bold, ` para código, etc)."
    )

    messages = [{"role": "system", "content": sys_prompt}] + conversation_history[uid]

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        max_tokens=1500,
        temperature=0.7,
    )

    reply = response.choices[0].message.content
    conversation_history[uid].append({"role": "assistant", "content": reply})
    return reply

# ─── EVENTOS ──────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user}")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="as tuas perguntas | /ajuda"
    ))
    try:
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)} comandos slash sincronizados")
    except Exception as e:
        print(f"❌ Erro ao sincronizar comandos: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    # Responde quando mencionado OU em DM OU no canal "geral-ia" / "bot"
    is_mentioned = bot.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_bot_channel = any(name in message.channel.name.lower() for name in ["bot", "ia", "assistente"]) if hasattr(message.channel, 'name') else False

    # Canal de checklist — interpretar comandos naturais
    is_checklist = hasattr(message.channel, 'name') and CHECKLIST_CHANNEL_NAME in message.channel.name.lower()
    is_tasks = hasattr(message.channel, 'name') and TASKS_CHANNEL_NAME in message.channel.name.lower()

    if is_checklist and not message.content.startswith("/"):
        await handle_checklist_message(message)
        return

    if is_tasks and not message.content.startswith("/"):
        await handle_task_message(message)
        return

    if is_mentioned or is_dm or is_bot_channel:
        # Remove a menção do texto
        content = message.content.replace(f"<@{bot.user.id}>", "").strip()
        if not content:
            await message.reply("Olá! Como posso ajudar? 👋")
            return

        async with message.channel.typing():
            try:
                reply = ask_groq(message.author.id, content)
                # Dividir resposta se for muito longa (Discord limite: 2000 chars)
                if len(reply) <= 1900:
                    await message.reply(reply)
                else:
                    chunks = [reply[i:i+1900] for i in range(0, len(reply), 1900)]
                    for i, chunk in enumerate(chunks):
                        if i == 0:
                            await message.reply(chunk)
                        else:
                            await message.channel.send(chunk)
            except Exception as e:
                await message.reply(f"❌ Erro ao processar a tua mensagem: `{e}`")

# ─── CHECKLIST NATURAL ────────────────────────────────────────────────────────
async def handle_checklist_message(message):
    guild_id = message.guild.id
    data = get_server_data(guild_id)
    content = message.content.lower()

    # Detetar intenção com Groq
    prompt = f"""
O utilizador disse: "{message.content}"
A checklist atual é: {json.dumps(data['checklist'], ensure_ascii=False)}

Determina a ação pretendida e responde APENAS com JSON válido:
- Se quer marcar um item como feito: {{"action": "check", "item": "nome do item"}}
- Se quer desmarcar: {{"action": "uncheck", "item": "nome do item"}}
- Se quer adicionar item: {{"action": "add", "item": "nome do item"}}
- Se quer remover item: {{"action": "remove", "item": "nome do item"}}
- Se quer ver a lista: {{"action": "show"}}
- Se quer limpar itens feitos: {{"action": "clear_done"}}
- Se não perceberes: {{"action": "unknown"}}
"""
    try:
        result_raw = ask_groq(message.author.id + 99999, prompt,
                              system_prompt="Respondes APENAS com JSON válido, sem texto extra.")
        result_raw = re.sub(r"```json|```", "", result_raw).strip()
        result = json.loads(result_raw)
        action = result.get("action")
        item_name = result.get("item", "").strip()

        if action == "add":
            data["checklist"].append({"text": item_name, "done": False})
            save_data(server_data)
            await message.add_reaction("✅")
            await show_checklist(message, data)

        elif action == "check":
            matched = False
            for item in data["checklist"]:
                if item_name.lower() in item["text"].lower():
                    item["done"] = True
                    matched = True
            if matched:
                save_data(server_data)
                await message.add_reaction("✅")
                await show_checklist(message, data)
            else:
                await message.reply(f"❓ Não encontrei '{item_name}' na checklist.")

        elif action == "uncheck":
            for item in data["checklist"]:
                if item_name.lower() in item["text"].lower():
                    item["done"] = False
            save_data(server_data)
            await message.add_reaction("🔄")
            await show_checklist(message, data)

        elif action == "remove":
            before = len(data["checklist"])
            data["checklist"] = [i for i in data["checklist"] if item_name.lower() not in i["text"].lower()]
            save_data(server_data)
            await message.add_reaction("🗑️")
            await show_checklist(message, data)

        elif action == "show":
            await show_checklist(message, data)

        elif action == "clear_done":
            data["checklist"] = [i for i in data["checklist"] if not i["done"]]
            save_data(server_data)
            await message.reply("🧹 Itens concluídos removidos!")
            await show_checklist(message, data)

        else:
            await message.reply("❓ Não percebi. Tenta: *'adiciona X'*, *'marca X como feito'*, *'mostra a lista'*")

    except Exception as e:
        await message.reply(f"❌ Erro: `{e}`")

async def show_checklist(message, data):
    if not data["checklist"]:
        await message.channel.send("📋 A checklist está vazia.")
        return
    lines = ["**📋 Checklist:**"]
    for item in data["checklist"]:
        icon = "✅" if item["done"] else "⬜"
        lines.append(f"{icon} {item['text']}")
    done_count = sum(1 for i in data["checklist"] if i["done"])
    total = len(data["checklist"])
    lines.append(f"\n*{done_count}/{total} concluídos*")
    await message.channel.send("\n".join(lines))

# ─── TAREFAS NATURAL ──────────────────────────────────────────────────────────
async def handle_task_message(message):
    guild_id = message.guild.id
    data = get_server_data(guild_id)

    prompt = f"""
O utilizador disse: "{message.content}"
As tarefas atuais são: {json.dumps(data['tasks'], ensure_ascii=False)}

Responde APENAS com JSON:
- Adicionar: {{"action": "add", "title": "título", "desc": "descrição opcional"}}
- Concluir: {{"action": "done", "title": "título"}}
- Remover: {{"action": "remove", "title": "título"}}
- Listar: {{"action": "show"}}
- Outro: {{"action": "unknown"}}
"""
    try:
        result_raw = ask_groq(message.author.id + 88888, prompt,
                              system_prompt="Respondes APENAS com JSON válido, sem texto extra.")
        result_raw = re.sub(r"```json|```", "", result_raw).strip()
        result = json.loads(result_raw)
        action = result.get("action")
        title = result.get("title", "").strip()
        desc = result.get("desc", "")

        if action == "add":
            data["tasks"].append({
                "title": title, "desc": desc,
                "done": False,
                "created": datetime.now().strftime("%d/%m/%Y %H:%M")
            })
            save_data(server_data)
            await message.add_reaction("📝")
            await show_tasks(message, data)

        elif action == "done":
            for task in data["tasks"]:
                if title.lower() in task["title"].lower():
                    task["done"] = True
            save_data(server_data)
            await message.add_reaction("✅")
            await show_tasks(message, data)

        elif action == "remove":
            data["tasks"] = [t for t in data["tasks"] if title.lower() not in t["title"].lower()]
            save_data(server_data)
            await message.add_reaction("🗑️")
            await show_tasks(message, data)

        elif action == "show":
            await show_tasks(message, data)

        else:
            await message.reply("❓ Não percebi. Tenta: *'adiciona tarefa X'*, *'marca X como feito'*, *'mostra tarefas'*")

    except Exception as e:
        await message.reply(f"❌ Erro: `{e}`")

async def show_tasks(message, data):
    if not data["tasks"]:
        await message.channel.send("📝 Sem tarefas.")
        return
    pending = [t for t in data["tasks"] if not t["done"]]
    done = [t for t in data["tasks"] if t["done"]]
    lines = ["**📝 Tarefas:**"]
    if pending:
        lines.append("**Pendentes:**")
        for t in pending:
            lines.append(f"⬜ **{t['title']}**" + (f" — {t['desc']}" if t['desc'] else ""))
    if done:
        lines.append("**Concluídas:**")
        for t in done:
            lines.append(f"✅ ~~{t['title']}~~")
    await message.channel.send("\n".join(lines))

# ─── COMANDOS SLASH ───────────────────────────────────────────────────────────
@bot.tree.command(name="perguntar", description="Faz uma pergunta ao assistente AI")
@app_commands.describe(pergunta="A tua pergunta")
async def slash_ask(interaction: discord.Interaction, pergunta: str):
    await interaction.response.defer()
    try:
        reply = ask_groq(interaction.user.id, pergunta)
        await interaction.followup.send(f"**Tu:** {pergunta}\n\n**Assistente:** {reply}")
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: `{e}`")

@bot.tree.command(name="limpar_historico", description="Apaga o histórico da tua conversa com o bot")
async def slash_clear(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    if uid in conversation_history:
        conversation_history[uid] = []
    await interaction.response.send_message("🗑️ Histórico apagado! Começamos do zero.", ephemeral=True)

@bot.tree.command(name="checklist", description="Mostra a checklist atual")
async def slash_checklist(interaction: discord.Interaction):
    data = get_server_data(interaction.guild_id)
    if not data["checklist"]:
        await interaction.response.send_message("📋 A checklist está vazia.", ephemeral=True)
        return
    lines = ["**📋 Checklist:**"]
    for item in data["checklist"]:
        icon = "✅" if item["done"] else "⬜"
        lines.append(f"{icon} {item['text']}")
    await interaction.response.send_message("\n".join(lines))

@bot.tree.command(name="tarefas", description="Mostra as tarefas atuais")
async def slash_tasks(interaction: discord.Interaction):
    data = get_server_data(interaction.guild_id)
    class FakeMessage:
        channel = interaction.channel
        guild = interaction.guild
        author = interaction.user
    await interaction.response.defer()
    await show_tasks(FakeMessage(), data)

@bot.tree.command(name="ajuda", description="Mostra como usar o bot")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 Assistente AI — Ajuda",
        description="Sou um assistente AI integrado no Discord!",
        color=0x5865F2
    )
    embed.add_field(
        name="💬 Chat",
        value=(
            "• Menciona-me: `@Bot pergunta`\n"
            "• Em DM: escreve diretamente\n"
            "• Em canais com 'bot' ou 'ia' no nome: escreve livremente\n"
            "• `/perguntar` — comando slash"
        ),
        inline=False
    )
    embed.add_field(
        name="✅ Checklist",
        value=(
            "No canal **#checklist**, escreve naturalmente:\n"
            "• *'adiciona comprar leite'*\n"
            "• *'marca comprar leite como feito'*\n"
            "• *'mostra a lista'*\n"
            "• *'limpa os itens feitos'*"
        ),
        inline=False
    )
    embed.add_field(
        name="📝 Tarefas",
        value=(
            "No canal **#tarefas**, escreve naturalmente:\n"
            "• *'adiciona tarefa: reunião amanhã'*\n"
            "• *'marca reunião como feita'*\n"
            "• *'mostra as tarefas'*"
        ),
        inline=False
    )
    embed.add_field(
        name="⚙️ Outros comandos",
        value=(
            "`/limpar_historico` — apaga o histórico da conversa\n"
            "`/checklist` — mostra a checklist\n"
            "`/tarefas` — mostra as tarefas"
        ),
        inline=False
    )
    embed.set_footer(text="Powered by Groq (LLaMA 3.3)")
    await interaction.response.send_message(embed=embed)

# ─── ARRANQUE ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
