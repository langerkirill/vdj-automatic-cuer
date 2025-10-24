# Automatic Music Cuer for VirtualDJ

An intelligent music analysis tool that uses Google's Gemini AI to automatically detect musical elements (drums, vocals, melody) and create cue points in your VirtualDJ library.

## ⚡ Quick Start

```bash
# 1. Run the setup script
./setup.sh

# 2. Analyze a track
python3 automatic_music_cuer_gemini.py "path/to/song.mp3"
```

That's it! The setup script will install everything and help you set up your API key.

## 🎯 What It Does

This script analyzes your music files and automatically creates:
- **Cue Points**: Marks important transitions (intro, drops, breakdowns, vocal entries, etc.)
- **Loops**: Creates DJ-friendly loop segments (drum loops, vocal loops, melodic loops)
- **Color-Coded Comments**: Labels each cue with the musical elements present, making it easy to filter and find specific sounds when DJing

## ⚠️ Platform Support

**Mac only** - This script automatically finds your VirtualDJ database at:
```
~/Library/Application Support/VirtualDJ/database.xml
```

For Windows/Linux support, you would need to manually specify the database path.

## 🎨 Color System (My Personal DJ Preferences)

The colors reflect my DJing style and help me quickly find the right transition points:

- **🔵 Blue** - Melodic only (piano, strings, synth, guitar, bass) - NO drums or vocals
  - *Use case: Smooth ambient transitions, building tension*

- **🟢 Green** - Melodic + drums - NO vocals
  - *Use case: Instrumental breaks, building energy without lyrics*

- **🟣 Purple** - Drums only (80%+ drums/percussion)
  - *Use case: Perfect for transitions, drum breaks, mixing between tracks*

- **🟡 Yellow** - Full mix (drums + melody + vocals)
  - *Use case: Peak energy moments, main sections of tracks*

- **🟠 Orange** - Vocals + melody - NO drums
  - *Use case: Acapella sections, vocal-focused moments*

### Why Color-Coded Comments Matter

In VirtualDJ, you can **filter cues by color**. This means during a live set, I can:
- Quickly jump to "drums only" sections (purple) when I need a clean transition
- Find "melodic only" sections (blue) for smooth ambient mixing
- Locate "full mix" moments (yellow) for peak energy drops

The comments are automatically added to each cue describing the exact musical elements, making it easy to remember what each color means.

## 🔧 Setup Instructions

### 1. Get a Gemini API Key

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Sign in with your Google account
3. Click **"Get API Key"** or **"Create API Key"**
4. Copy your API key (starts with `AIza...`)

**Note**: Gemini has a free tier with generous limits. For music analysis, the free tier is usually sufficient for personal DJ libraries.

### 2. Install Dependencies

```bash
cd claude_scripts
pip3 install -r requirements.txt
```

Required packages:
- `google-generativeai` - For Gemini AI integration
- `python-dotenv` - For API key management
- `pydantic` - For structured data parsing

### 3. Configure Your API Key

Create a `.env` file in the `claude_scripts` directory:

```bash
echo "GEMINI_API_KEY=your_api_key_here" > .env
```

Replace `your_api_key_here` with your actual Gemini API key.

**Security Note**: The `.env` file should NOT be committed to git. Add it to your `.gitignore`:
```bash
echo ".env" >> .gitignore
```

## 🚀 Usage

### Basic Usage

```bash
# Analyze a single track (dry-run to preview changes)
python3 automatic_music_cuer_gemini.py --dry-run "path/to/song.mp3"

# Analyze and update VirtualDJ database
python3 automatic_music_cuer_gemini.py "path/to/song.mp3"
```

### Advanced Options

```bash
# Process multiple files
for file in ~/Music/DJ/NewTracks/*.mp3; do
    python3 automatic_music_cuer_gemini.py "$file"
done

# Dry-run multiple files first to review
for file in ~/Music/DJ/NewTracks/*.mp3; do
    python3 automatic_music_cuer_gemini.py --dry-run "$file"
done
```

## 🔍 How It Works

1. **Upload**: Sends your audio file to Gemini AI
2. **Analysis**: Gemini listens to the entire track and identifies:
   - Musical elements (drums, bass, vocals, synth, piano)
   - Timing of transitions (when elements enter/exit)
   - Loop-friendly sections for DJing
3. **Color Assignment**: Based on detected elements, assigns colors according to the system above
4. **Database Update**: Safely writes cue points to your VirtualDJ database
5. **Backup**: Automatically creates timestamped backups before any changes

## 🎵 What Gets Created

### Cue Points (5-6 per track)
- Intro
- Drums In
- Vocal Entry
- Breakdown
- Drop/Build-up
- Outro

### Loop Segments (3 per track)
- **Drum Loop** (16-32 beats): Drums-only section for transitions
- **Vocal Loop** (16-32 beats): Prominent vocals for crowd engagement
- **Melodic Loop** (16-32 beats): Melody without drums/vocals for smooth mixing

## 📝 Output Format

Each cue includes:
- **Timestamp**: Precise timing (rounded to 0.01s)
- **Name**: Descriptive label (e.g., "Drums In", "Vocal Drop")
- **Color**: Based on musical elements present
- **Comment**: Lists detected elements (e.g., "drums, bass, synth")

## ⚙️ VirtualDJ Integration

After running the script:

1. **Refresh VirtualDJ database**:
   - Press `Cmd+Option+R` (Mac)
   - Or: Options → Reload Database

2. **View your cues**:
   - Open the track in VirtualDJ
   - Cues appear in the waveform with assigned colors
   - Hover over cues to see comments

3. **Filter by color**:
   - Use VirtualDJ's cue filter to show only specific colors
   - Perfect for finding transition points during live sets

## 🛡️ Safety Features

- **Automatic Backups**: Creates timestamped backup before every change
- **Dry-Run Mode**: Preview changes without modifying database
- **XML Validation**: Validates database integrity before saving
- **Atomic Writes**: Uses temporary files to prevent corruption
- **Retry Logic**: Handles network errors gracefully with automatic retries

## 🐛 Troubleshooting

### "GEMINI_API_KEY not found"
- Ensure `.env` file exists in `claude_scripts` directory
- Check that API key is correctly formatted: `GEMINI_API_KEY=AIza...`

### "Database not found"
- Verify VirtualDJ is installed and has been run at least once
- Check path: `~/Library/Application Support/VirtualDJ/database.xml`

### "Upload failed" or "SSL errors"
- Check internet connection
- Script will automatically retry up to 5 times
- Large files may take longer to upload

### Cues not appearing in VirtualDJ
- Press `Cmd+Option+R` to refresh database
- Ensure file path in VirtualDJ matches file analyzed
- Check dry-run output for any error messages

## 📊 Example Output

```
🎵 Automatic Music Cuer initialized with Gemini
📁 VDJ Database: ~/Library/Application Support/VirtualDJ/database.xml
✅ Database backed up to: database.xml.backup.20250124_143022
🔍 Analyzing song.mp3 with Gemini...
📤 Uploading audio file (8.2 MB)...
✅ Upload complete
🤖 Analyzing audio with Gemini...
✅ Analysis complete: 6 cues, 3 loops

Cue 1: Intro at 0.00s - [synth] - Color: blue
Cue 2: Drums In at 45.23s - [drums, synth] - Color: green
Cue 3: Vocal Drop at 92.15s - [drums, vocals, synth] - Color: yellow
...

✅ Successfully updated VDJ database with cues and loops
```

## 🎛️ Customization

To modify the color system for your preferences, edit the `color_mappings` dictionary in [automatic_music_cuer_gemini.py](automatic_music_cuer_gemini.py:96):

```python
self.color_mappings = {
    "blue": "4278190335",    # Your custom color
    "green": "4278255360",   # Your custom color
    ...
}
```

And update the color rules in the Gemini prompt (lines 222-227).
