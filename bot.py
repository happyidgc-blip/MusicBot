import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatType
from pytgcalls import PyTgCalls
from pytgcalls.types import AudioPiped
from pytgcalls.exceptions import NoActiveGroupCall
from yt_dlp import YoutubeDL
import re
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient

# ==================== CONFIG ====================
API_ID = 33798531
API_HASH = "5daa87783e064820a001056e97891e6e"
BOT_TOKEN = "8544699721:AAFR1w33OXLeGHjmjQfkRYOrEKk7lfIsPn4"
OWNER_ID = 7167704900

# MongoDB Connection
MONGO_URL = "mongodb+srv://rj5706603:O95nvJYxapyDHfkw@cluster0.fzmckei.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client.music_bot
users_col = db.users
playlist_col = db.playlists
settings_col = db.settings

# Pyrogram Client
app = Client(
    "music_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# PyTgCalls for voice chat
call = PyTgCalls(app)

# Queue System
queues = {}
current_playing = {}
loop_status = {}
volume = {}

# ==================== HELPERS ====================
async def is_admin(chat_id, user_id):
    chat = await app.get_chat(chat_id)
    member = await chat.get_member(user_id)
    return member.status in ["creator", "administrator"] or user_id == OWNER_ID

async def download_audio(url):
    """Download audio from YouTube"""
    os.makedirs("downloads", exist_ok=True)
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'extractaudio': True,
        'audioformat': 'mp3',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            filename = filename.replace('.webm', '.mp3').replace('.m4a', '.mp3')
            return filename, info['title'], info['duration']
    except Exception as e:
        print(f"Download error: {e}")
        return None, None, None

async def get_youtube_url(query):
    """Search YouTube and get URL"""
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with YoutubeDL(ydl_opts) as ydl:
            if 'youtube.com' in query or 'youtu.be' in query:
                info = ydl.extract_info(query, download=False)
                return query, info['title'], info.get('duration', 0)
            else:
                info = ydl.extract_info(f"ytsearch:{query}", download=False)
                if 'entries' in info and info['entries']:
                    video = info['entries'][0]
                    return video['webpage_url'], video['title'], video.get('duration', 0)
    except Exception as e:
        print(f"Search error: {e}")
        return None, None, None
    return None, None, None

async def play_song(chat_id, url, title, duration):
    """Play a song in voice chat"""
    try:
        audio_file, _, _ = await download_audio(url)
        if audio_file:
            await call.change_stream(chat_id, AudioPiped(audio_file))
            current_playing[chat_id] = {'title': title, 'duration': duration, 'url': url}
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("⏸ Pause", callback_data="pause"),
                 InlineKeyboardButton("▶️ Resume", callback_data="resume")],
                [InlineKeyboardButton("⏭ Skip", callback_data="skip"),
                 InlineKeyboardButton("🔁 Loop", callback_data="loop")],
                [InlineKeyboardButton("📜 Queue", callback_data="queue"),
                 InlineKeyboardButton("🗑 Stop", callback_data="stop")]
            ])
            
            await app.send_message(chat_id, 
                f"🎵 **Now Playing**\n\n"
                f"🎧 **Title:** {title}\n"
                f"⏱️ **Duration:** {duration//60}:{duration%60:02d}\n\n"
                f"📢 **Requested by:** @{message.from_user.username if message else 'user'}",
                reply_markup=keyboard)
            return True
    except Exception as e:
        print(f"Play error: {e}")
        return False

async def play_next(chat_id):
    """Play next song in queue"""
    if chat_id in queues and queues[chat_id]:
        next_song = queues[chat_id].pop(0)
        await play_song(chat_id, next_song['url'], next_song['title'], next_song['duration'])
    else:
        current_playing.pop(chat_id, None)
        await call.leave_call(chat_id)

# ==================== BOT COMMANDS ====================
@app.on_message(filters.command("start"))
async def start_command(client, message):
    user_id = message.from_user.id
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"username": message.from_user.username, "last_active": datetime.now()}},
        upsert=True
    )
    
    await message.reply_text(
        "🎵 **Voice Chat Music Bot** 🎵\n\n"
        "**Commands:**\n"
        "🎧 `/play <song name/url>` - Play music\n"
        "⏸ `/pause` - Pause current song\n"
        "▶️ `/resume` - Resume playing\n"
        "⏭ `/skip` - Skip current song\n"
        "🔁 `/loop` - Toggle loop\n"
        "📜 `/queue` - Show queue\n"
        "🗑 `/stop` - Stop music\n"
        "⚡ `/join` - Join voice chat\n"
        "👋 `/leave` - Leave voice chat\n\n"
        "**Made by:** @ZenoRealWebs"
    )

@app.on_message(filters.command("play") & filters.group)
async def play_command(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    # Check if user is in voice chat
    try:
        user = await app.get_chat_member(chat_id, user_id)
        if user.voice_chat is None:
            await message.reply_text("❌ You need to join a voice chat first!")
            return
    except:
        await message.reply_text("❌ You need to join a voice chat first!")
        return
    
    query = message.text.split(" ", 1)[1] if len(message.text.split()) > 1 else None
    
    if not query:
        await message.reply_text("❌ Usage: `/play <song name or YouTube URL>`")
        return
    
    # Get YouTube URL
    url, title, duration = await get_youtube_url(query)
    
    if not url:
        await message.reply_text("❌ Could not find the song!")
        return
    
    # Check if already playing
    if chat_id in current_playing:
        # Add to queue
        if chat_id not in queues:
            queues[chat_id] = []
        queues[chat_id].append({'url': url, 'title': title, 'duration': duration})
        position = len(queues[chat_id])
        await message.reply_text(f"✅ Added to queue!\n\n🎧 **{title}**\n📍 Position: {position}")
    else:
        # Play now
        success = await play_song(chat_id, url, title, duration)
        if success:
            await message.reply_text(f"🎵 **Now Playing:**\n{title}")

@app.on_message(filters.command("pause") & filters.group)
async def pause_command(client, message):
    chat_id = message.chat.id
    await call.pause_stream(chat_id)
    await message.reply_text("⏸ **Paused**")

@app.on_message(filters.command("resume") & filters.group)
async def resume_command(client, message):
    chat_id = message.chat.id
    await call.resume_stream(chat_id)
    await message.reply_text("▶️ **Resumed**")

@app.on_message(filters.command("skip") & filters.group)
async def skip_command(client, message):
    chat_id = message.chat.id
    if chat_id in current_playing:
        await play_next(chat_id)
        await message.reply_text("⏭ **Skipped**")

@app.on_message(filters.command("stop") & filters.group)
async def stop_command(client, message):
    chat_id = message.chat.id
    if chat_id in queues:
        queues[chat_id].clear()
    current_playing.pop(chat_id, None)
    await call.leave_call(chat_id)
    await message.reply_text("🗑 **Stopped and left voice chat**")

@app.on_message(filters.command("queue") & filters.group)
async def queue_command(client, message):
    chat_id = message.chat.id
    
    if chat_id not in queues or not queues[chat_id]:
        await message.reply_text("📜 **Queue is empty!**")
        return
    
    queue_text = "📜 **Current Queue**\n\n"
    for i, song in enumerate(queues[chat_id][:10], 1):
        duration_min = song['duration'] // 60
        duration_sec = song['duration'] % 60
        queue_text += f"{i}. {song['title']} [{duration_min}:{duration_sec:02d}]\n"
    
    await message.reply_text(queue_text)

@app.on_message(filters.command("join") & filters.group)
async def join_command(client, message):
    chat_id = message.chat.id
    try:
        await call.join_call(chat_id)
        await message.reply_text("✅ **Joined voice chat!**")
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")

@app.on_message(filters.command("leave") & filters.group)
async def leave_command(client, message):
    chat_id = message.chat.id
    try:
        await call.leave_call(chat_id)
        current_playing.pop(chat_id, None)
        if chat_id in queues:
            queues[chat_id].clear()
        await message.reply_text("👋 **Left voice chat!**")
    except:
        await message.reply_text("❌ Not in voice chat!")

@app.on_message(filters.command("loop") & filters.group)
async def loop_command(client, message):
    chat_id = message.chat.id
    loop_status[chat_id] = not loop_status.get(chat_id, False)
    status = "ON" if loop_status[chat_id] else "OFF"
    await message.reply_text(f"🔁 **Loop: {status}**")

@app.on_message(filters.command("ping") & filters.group)
async def ping_command(client, message):
    start = datetime.now()
    msg = await message.reply_text("🏓 Pinging...")
    end = datetime.now()
    ping = (end - start).microseconds / 1000
    await msg.edit_text(f"🏓 **Pong!**\n\n📊 **Latency:** {ping:.2f}ms")

@app.on_message(filters.command("stats") & filters.group)
async def stats_command(client, message):
    total_users = await users_col.count_documents({})
    await message.reply_text(
        f"📊 **Bot Statistics**\n\n"
        f"👥 **Total Users:** {total_users}\n"
        f"🎵 **Active Calls:** {len(current_playing)}\n"
        f"📜 **Queue Length:** {sum(len(q) for q in queues.values())}"
    )

@app.on_message(filters.command("admin") & filters.user(OWNER_ID))
async def admin_command(client, message):
    await message.reply_text(
        "👑 **Admin Commands**\n\n"
        "`/broadcast <msg>` - Send message to all users\n"
        "`/stats` - View bot statistics\n"
        "`/leave_all` - Leave all voice chats"
    )

@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast_command(client, message):
    msg = message.text.split(" ", 1)[1] if len(message.text.split()) > 1 else None
    if not msg:
        await message.reply_text("❌ Usage: `/broadcast <message>`")
        return
    
    count = 0
    async for user in users_col.find():
        try:
            await app.send_message(user['user_id'], msg)
            count += 1
        except:
            pass
    
    await message.reply_text(f"✅ **Broadcast sent to {count} users!**")

# ==================== CALLBACK HANDLERS ====================
@app.on_callback_query()
async def callback_handler(client, callback_query):
    data = callback_query.data
    chat_id = callback_query.message.chat.id
    
    if data == "pause":
        await call.pause_stream(chat_id)
        await callback_query.answer("⏸ Paused")
    
    elif data == "resume":
        await call.resume_stream(chat_id)
        await callback_query.answer("▶️ Resumed")
    
    elif data == "skip":
        await play_next(chat_id)
        await callback_query.answer("⏭ Skipped")
    
    elif data == "stop":
        if chat_id in queues:
            queues[chat_id].clear()
        current_playing.pop(chat_id, None)
        await call.leave_call(chat_id)
        await callback_query.answer("🗑 Stopped")
    
    elif data == "queue":
        if chat_id not in queues or not queues[chat_id]:
            await callback_query.answer("Queue is empty!")
        else:
            await callback_query.answer(f"Queue has {len(queues.get(chat_id, []))} songs")
    
    elif data == "loop":
        loop_status[chat_id] = not loop_status.get(chat_id, False)
        status = "ON" if loop_status[chat_id] else "OFF"
        await callback_query.answer(f"Loop {status}")

# ==================== VOICE CHAT HANDLERS ====================
@call.on_stream_end()
async def on_stream_end(chat_id):
    if loop_status.get(chat_id, False) and chat_id in current_playing:
        # Replay current song
        song = current_playing[chat_id]
        await play_song(chat_id, song['url'], song['title'], song['duration'])
    else:
        await play_next(chat_id)

@call.on_closed()
async def on_call_closed(chat_id):
    current_playing.pop(chat_id, None)
    if chat_id in queues:
        queues[chat_id].clear()

# ==================== RUN BOT ====================
async def main():
    await call.start()
    await app.start()
    print("🎵 Music Bot Started!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
