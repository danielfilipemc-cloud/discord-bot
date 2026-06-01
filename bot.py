import discord
from discord.ext import commands
from discord import app_commands
import os
import json
import re
from groq import Groq
from tavily import TavilyClient
from datetime import datetime

# ─── CONFIG ───────────────────────────────────────────────────────────────────
DISCORD_TOKEN  = os.environ.get("DISCORD_TOKEN")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
GROQ_MODEL     = "llama-3.3-70b-versatile"

CHECKLIST_CHANNEL_NAME = "checklist"
TASKS_CHANNEL_NAME     = "tarefas"

# ─── CLIENTES ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot         = commands.Bot(command_prefix="!", intents=intents)
groq_client = Groq(api_key=GROQ_API_KEY)
tavily      = TavilyClient(api_key=TAVILY_API_KEY)

# ─── ESTADO ───────────────────────────────────────────────────────────────────
conversation_history = {}
MAX_HISTORY = 20
DATA_FILE   = "data.json"

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

# ─── PESQUISA WEB (sempre ativa) ──────────────────────────────────────────────
def web_search(query: str) -> str:
    try:
        results = tavily.search(query=query, max_results=5, search_depth="basic")
        output = []
        for r in results.get("results", []):
            title   = r.get("title", "")
            content = r.get("content", "")[:500]
            url     = r.get("url", "")
            output.append(f"Título: {title}\nConteúdo: {content}\nURL: {url}")
        return "\n\n---\n\n".join(output) if output else "Sem resultados."
    except Exception as e:
        return f"Erro na pesquisa web: {e}"

# ─── GROQ CHAT ────────────────────────────────────────────────────────────────
def ask_groq(user_id, user_message, system_prompt=None, search_context=None):
    uid = str(user_id)
    if uid not in conversation_history:
        conversation_history[uid] = []

    if search_context:
        full_message = (
            f"[Informação atual da web, data de hoje: {datetime.now().strftime('%d/%m/%Y')}]\n"
            f"{search_context}\n\n"
            f"[Pergunta do utilizador]\n{user_message}"
        )
    else:
        full_message = user_message

    conversation_history[uid].append({"role": "user", "content": full_message})

    if len(conversation_history[uid]) > MAX_HISTORY:
        conversation_history[uid] = conversation_history[uid][-MAX_HISTORY:]

    sys_prompt = system_prompt or (
        "És um assistente AI integrado no Discord. "
        "Respondes SEMPRE em português europeu. "
        "Tens acesso a informação atualizada da internet que é fornecida antes de cada pergunta. "
        "NUNCA digas que não tens acesso à internet — tens, através dos resultados de pesquisa fornecidos. "
        "Usa sempre a informação da web para responder com dados atuais e precisos. "
        "Cita as fontes quando relevante. "
        "Usa formatação Discord: **negrito**, `código`, etc."
    )

    messages = [{"role": "system", "content": sys_prompt}] + conversation_history[uid]

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        max_tokens=1500,
        temperature=0.7,
    )

    reply = response.choices[0].message.content
    conversation_history[uid][-1] = {"role": "user", "content": user_message}
    conversation_history[uid].append({"role": "assistant", "content": reply})
    return reply

# ─── RESPOSTA COM PESQUISA AUTOMÁTICA ─────────────────────────────────────────
async def respond(content: str, user_id: int, reply_func):
    # Pesquisa sempre na web
    search_context = web_search(content)
    reply = ask_groq(user_id, content, search_context=search_context)

    if len(reply) <= 1900:
        await reply_func(reply)
    else:
        chunks = [reply[i:i+1900] for i in range(0, len(reply), 1900)]
        for chunk in chunks:
            await reply_func(chunk)

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
        print(f"✅ {len(synced)} comandos sincronizados")
    except Exception as e:
        print(f"❌ Erro sync: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    is_mentioned   = bot.user in message.mentions
    is_dm          = isinstance(message.channel, discord.DMChannel)
    is_bot_channel = hasattr(message.channel, 'name') and any(
        n in message.channel.name.lower() for n in ["bot", "ia", "assistente"]
    )
    is_checklist = hasattr(message.channel, 'name') and CHECKLIST_CHANNEL_NAME in message.channel.name.lower()
    is_tasks     = hasattr(message.channel, 'name') and TASKS_CHANNEL_NAME in message.channel.name.lower()

    if is_checklist and not message.content.startswith("/"):
        await handle_checklist_message(message)
        return

    if is_tasks and not message.content.startswith("/"):
        await handle_task_message(message)
        return

    if is_mentioned or is_dm or is_bot_channel:
        content = message.content.replace(f"<@{bot.user.id}>", "").strip()
        if not content:
            await message.reply("Olá! Como posso ajudar? 👋")
            return

        async with message.channel.typing():
            try:
                await respond(content, message.author.id, message.reply)
            except Exception as e:
                await message.reply(f"❌ Erro: `{e}`")

# ─── CHECKLIST ────────────────────────────────────────────────────────────────
async def handle_checklist_message(message):
    guild_id = message.guild.id
    data = get_server_data(guild_id)

    prompt = f"""
Utilizador disse: "{message.content}"
Checklist atual: {json.dumps(data['checklist'], ensure_ascii=False)}

Responde APENAS com JSON:
- {{"action": "add", "item": "nome"}}
- {{"action": "check", "item": "nome"}}
- {{"action": "uncheck", "item": "nome"}}
- {{"action": "remove", "item": "nome"}}
- {{"action": "show"}}
- {{"action": "clear_done"}}
- {{"action": "unknown"}}
"""
    try:
        raw = ask_groq(message.author.id + 99999, prompt,
                       system_prompt="Respondes APENAS com JSON válido, sem texto extra.")
        raw = re.sub(r"```json|```", "", raw).strip()
        result = json.loads(raw)
        action    = result.get("action")
        item_name = result.get("item", "").strip()

        if action == "add":
            data["checklist"].append({"text": item_name, "done": False})
            save_data(server_data)
            await message.add_reaction("✅")
            await show_checklist(message, data)
        elif action == "check":
            matched = any(item_name.lower() in i["text"].lower() for i in data["checklist"])
            for item in data["checklist"]:
                if item_name.lower() in item["text"].lower():
                    item["done"] = True
            if matched:
                save_data(server_data)
                await message.add_reaction("✅")
                await show_checklist(message, data)
            else:
                await message.reply(f"❓ Não encontrei '{item_name}'.")
        elif action == "uncheck":
            for item in data["checklist"]:
                if item_name.lower() in item["text"].lower():
                    item["done"] = False
            save_data(server_data)
            await message.add_reaction("🔄")
            await show_checklist(message, data)
        elif action == "remove":
            data["checklist"] = [i for i in data["checklist"] if item_name.lower() not in i["text"].lower()]
            save_data(server_data)
            await message.add_reaction("🗑️")
            await show_checklist(message, data)
        elif action == "show":
            await show_checklist(message, data)
        elif action == "clear_done":
            data["checklist"] = [i for i in data["checklist"] if not i["done"]]
            save_data(server_data)
            await show_checklist(message, data)
        else:
            await message.reply("❓ Tenta: *'adiciona X'*, *'marca X como feito'*, *'mostra a lista'*")
    except Exception as e:
        await message.reply(f"❌ Erro: `{e}`")

async def show_checklist(message, data):
    if not data["checklist"]:
        await message.channel.send("📋 Checklist vazia.")
        return
    lines = ["**📋 Checklist:**"]
    for item in data["checklist"]:
        lines.append(f"{'✅' if item['done'] else '⬜'} {item['text']}")
    done = sum(1 for i in data["checklist"] if i["done"])
    lines.append(f"\n*{done}/{len(data['checklist'])} concluídos*")
    await message.channel.send("\n".join(lines))

# ─── TAREFAS ──────────────────────────────────────────────────────────────────
async def handle_task_message(message):
    guild_id = message.guild.id
    data = get_server_data(guild_id)

    prompt = f"""
Utilizador disse: "{message.content}"
Tarefas: {json.dumps(data['tasks'], ensure_ascii=False)}

Responde APENAS com JSON:
- {{"action": "add", "title": "título", "desc": "opcional"}}
- {{"action": "done", "title": "título"}}
- {{"action": "remove", "title": "título"}}
- {{"action": "show"}}
- {{"action": "unknown"}}
"""
    try:
        raw = ask_groq(message.author.id + 88888, prompt,
                       system_prompt="Respondes APENAS com JSON válido, sem texto extra.")
        raw = re.sub(r"```json|```", "", raw).strip()
        result = json.loads(raw)
        action = result.get("action")
        title  = result.get("title", "").strip()
        desc   = result.get("desc", "")

        if action == "add":
            data["tasks"].append({"title": title, "desc": desc, "done": False,
                                  "created": datetime.now().strftime("%d/%m/%Y %H:%M")})
            save_data(server_data)
            await message.add_reaction("📝")
            await show_tasks(message, data)
        elif action == "done":
            for t in data["tasks"]:
                if title.lower() in t["title"].lower():
                    t["done"] = True
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
            await message.reply("❓ Tenta: *'adiciona tarefa X'*, *'marca X como feita'*")
    except Exception as e:
        await message.reply(f"❌ Erro: `{e}`")

async def show_tasks(message, data):
    if not data["tasks"]:
        await message.channel.send("📝 Sem tarefas.")
        return
    pending = [t for t in data["tasks"] if not t["done"]]
    done    = [t for t in data["tasks"] if t["done"]]
    lines   = ["**📝 Tarefas:**"]
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
        search_context = web_search(pergunta)
        reply = ask_groq(interaction.user.id, pergunta, search_context=search_context)
        await interaction.followup.send(f"**Tu:** {pergunta}\n\n{reply}")
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: `{e}`")

@bot.tree.command(name="limpar_historico", description="Apaga o histórico da conversa")
async def slash_clear(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    if uid in conversation_history:
        conversation_history[uid] = []
    await interaction.response.send_message("🗑️ Histórico apagado!", ephemeral=True)

@bot.tree.command(name="checklist", description="Mostra a checklist")
async def slash_checklist(interaction: discord.Interaction):
    data = get_server_data(interaction.guild_id)
    if not data["checklist"]:
        await interaction.response.send_message("📋 Checklist vazia.", ephemeral=True)
        return
    lines = ["**📋 Checklist:**"]
    for item in data["checklist"]:
        lines.append(f"{'✅' if item['done'] else '⬜'} {item['text']}")
    await interaction.response.send_message("\n".join(lines))

@bot.tree.command(name="tarefas", description="Mostra as tarefas")
async def slash_tasks(interaction: discord.Interaction):
    await interaction.response.defer()
    data = get_server_data(interaction.guild_id)
    if not data["tasks"]:
        await interaction.followup.send("📝 Sem tarefas.")
        return
    pending = [t for t in data["tasks"] if not t["done"]]
    done    = [t for t in data["tasks"] if t["done"]]
    lines   = ["**📝 Tarefas:**"]
    if pending:
        lines.append("**Pendentes:**")
        for t in pending:
            lines.append(f"⬜ **{t['title']}**" + (f" — {t['desc']}" if t['desc'] else ""))
    if done:
        lines.append("**Concluídas:**")
        for t in done:
            lines.append(f"✅ ~~{t['title']}~~")
    await interaction.followup.send("\n".join(lines))

@bot.tree.command(name="ajuda", description="Como usar o bot")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(title="🤖 Assistente AI — Ajuda", color=0x5865F2)
    embed.add_field(name="💬 Chat", value=(
        "• Menciona-me: `@Bot pergunta`\n"
        "• Em DM: escreve diretamente\n"
        "• Canais com 'bot' ou 'ia' no nome: escreve livremente"
    ), inline=False)
    embed.add_field(name="🔍 Pesquisa Web", value="Pesquiso sempre na web antes de responder — tens sempre info atualizada!", inline=False)
    embed.add_field(name="✅ Checklist (#checklist)", value=(
        "• *'adiciona X'* • *'marca X como feito'*\n• *'mostra a lista'* • *'limpa os feitos'*"
    ), inline=False)
    embed.add_field(name="📝 Tarefas (#tarefas)", value=(
        "• *'adiciona tarefa X'* • *'marca X como feita'*\n• *'mostra as tarefas'*"
    ), inline=False)
    embed.add_field(name="⚙️ Comandos", value="`/perguntar` `/checklist` `/tarefas` `/limpar_historico`", inline=False)
    embed.set_footer(text="Powered by Groq LLaMA 3.3 + Tavily Search")
    await interaction.response.send_message(embed=embed)

# ─── ARRANQUE ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
