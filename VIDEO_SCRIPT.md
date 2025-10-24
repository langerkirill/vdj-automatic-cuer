# Video Overview Script - Automatic Music Cuer

## Introduction (30 seconds)
"Hey everyone! Today I'm showing you my automatic music cuing system for VirtualDJ. This tool uses Google's Gemini AI to analyze your tracks and automatically create color-coded cue points based on the musical elements - drums, vocals, and melody."

## Prerequisites (1 minute)

### ⚠️ IMPORTANT: Beatgrid Alignment Required
"Before running this script on any song, you MUST align the beatgrid in VirtualDJ first. The script relies on VirtualDJ's beatgrid data - specifically, it looks for the '1' markers to align cues properly. If your beatgrid isn't set correctly, your cues will be off."

**Show on screen:**
- Open VirtualDJ
- Load a track
- Show beatgrid alignment process
- Point out the "1" markers on downbeats

### Song Must Be in VirtualDJ Database
"The song needs to exist in your VirtualDJ library before you run the script. The script reads BPM and timing information from the VDJ database, so make sure you've added the track to VirtualDJ and analyzed it first."

**Show on screen:**
- VirtualDJ library browser
- Track with BPM and beatgrid analyzed

## How to Use (2 minutes)

### Single File Processing
"You can drag and drop a single audio file to process just one track."

**Show on screen:**
```bash
python3 automatic_music_cuer_gemini.py "~/Music/song.mp3"
```

### Batch Processing (Entire Folders)
"Or you can drag an entire folder to process multiple tracks at once. This is super convenient when you've just downloaded a pack of new music."

**Show on screen:**
```bash
python3 automatic_music_cuer_gemini.py ~/Music/NewTracks/
```

**IMPORTANT - Cost Warning:**
"Keep in mind this uses the Gemini API, which does cost money. In my experience, it's about **$1 per 10 songs**, give or take. Not expensive, but something to be aware of if you're processing hundreds of tracks."

**Show on screen:**
- Terminal output showing multiple files being processed
- Mention: "Always use --dry-run first to preview changes"

## The Color System (1 minute)

"Here's how the color coding works - this is based on MY personal DJ preferences, but you can customize it:"

- **Blue** - Only melody (piano, synth, strings) - perfect for ambient transitions
- **Green** - Melody plus drums, no vocals - great for instrumental builds
- **Purple** - Drums only - THE BEST for mixing between tracks
- **Yellow** - Full mix with everything - peak energy moments
- **Orange** - Vocals and melody, no drums - for acapella sections

**Show on screen:**
- VirtualDJ waveform with colored cue points
- Demonstrate filtering by color during a mix

"The key thing here is you can filter by color in VirtualDJ during your set, so you can instantly jump to drum-only sections or melodic breaks."

## Customization (1 minute)

### Prompt Engineering
"The AI prompting is really easy to customize. If you open the script, you'll see the prompt around line 175. You can adjust it to match your specific music style or DJ preferences."

**Show on screen:**
```python
# Example: The prompt section in the code
prompt = f"""
You are analyzing a DJ track for precise cue point placement.
...
Color Rules (be strict):
- blue: Only melody, NO drums, NO vocals
...
"""
```

"For example, if you play techno instead of zouk, you might want different loop lengths or different color assignments. Just edit the prompt to describe what you want."

## Safety Features (1 minute)

### Automatic Database Backups
"Every single time you run this script, it creates a timestamped backup of your VirtualDJ database. So if something goes wrong, you can always restore from the backup."

**Show on screen:**
```
✅ Database backed up to: database.xml.backup.20250124_143022
```

"The backups are stored right next to your database file, so you can easily find them."

### ⚠️ Don't Edit While Running
"IMPORTANT: While the script is running, **do not make any changes in VirtualDJ**. Any changes you make won't be saved because the script will overwrite them when it finishes. Just let it complete, then refresh your database with Cmd+Option+R on Mac."

**Show on screen:**
- Text overlay: "⚠️ DO NOT EDIT IN VDJ WHILE SCRIPT IS RUNNING"
- Show the database refresh shortcut

## Results Demo (1 minute)

"Let me show you the actual results. Here's a track before and after processing."

**Show on screen:**
- Track with no cues (before)
- Same track with color-coded cues (after)
- Play through and demonstrate jumping between cue points
- Show the comments on each cue point showing detected elements

"As you can see, it found the intro, the drum entry, the vocal drop, and even created loop segments I can use for mixing."

## Closing (30 seconds)

"That's it! This tool has saved me HOURS of manual cue point creation. If you want to try it yourself, check out the README in the GitHub repo - I've included full setup instructions for getting your Gemini API key and installing everything."

"Questions? Drop them in the comments. Happy mixing!"

---

## B-Roll Suggestions

- Close-ups of VirtualDJ interface with colored cues
- Terminal showing script output
- Side-by-side comparison: manual cuing vs automatic
- Live DJ set using the colored cue filters
- The `.env` file setup process
- Gemini API dashboard showing usage/costs

## On-Screen Text Overlays

- "Beatgrid alignment required ⚠️"
- "~$1 per 10 songs"
- "Automatic backups ✅"
- "Don't edit VDJ while running ⚠️"
- "Cmd+Option+R to refresh database"
- "Customize colors + prompts"
- GitHub repo link

## Key Timestamps to Include

- 0:00 - Introduction
- 0:30 - Prerequisites (beatgrid + VDJ database)
- 1:30 - Usage (single file vs batch)
- 3:30 - Color system explanation
- 4:30 - Customization tips
- 5:30 - Safety features
- 6:30 - Results demo
- 7:30 - Closing

---

## Technical Details to Mention (Optional/Advanced Section)

- Uses Gemini 2.5 Pro for audio analysis
- Structured JSON output for reliability
- Automatic retry logic for network errors
- XML sanitization to prevent database corruption
- CRLF line ending preservation for VDJ compatibility
- Atomic file writing (temp file → validate → replace)

## Common Questions to Address

**Q: Does this work on Windows?**
A: Currently Mac-only, but the database path can be manually specified for Windows.

**Q: What audio formats are supported?**
A: MP3, FLAC, M4A, WAV - anything VirtualDJ supports.

**Q: Can I customize the colors?**
A: Absolutely! Edit the color_mappings dictionary and the prompt.

**Q: What if the cues are wrong?**
A: Use --dry-run first to preview. You can also manually adjust in VDJ after.

**Q: How accurate is the AI?**
A: In my testing, about 85-90% accurate. Beatgrid alignment is critical for timing accuracy.
