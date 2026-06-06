# Automatic Music Cuer for VirtualDJ

An intelligent music analysis tool that uses Google's Gemini AI to automatically detect musical elements (drums, vocals, melody) and create cue points in your VirtualDJ library.

## Video Walkthrough

[![Watch the walkthrough video](https://img.youtube.com/vi/8868lOUFJQA/maxresdefault.jpg)](https://youtu.be/8868lOUFJQA)

Click the image above to watch the full walkthrough on YouTube.

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/langerkirill/vdj-automatic-cuer.git
cd vdj-automatic-cuer

# 2. Run the setup script
./setup.sh

# 3. Activate the virtual environment
source venv/bin/activate

# 4. Analyze a track
python3 automatic_music_cuer_gemini.py "path/to/song.mp3"
```

That's it! The setup script will install everything and help you set up your API key.

## What It Does

This script analyzes your music files and automatically creates:

- **Cue Points**: Marks important transitions (intro, drops, breakdowns, vocal entries, etc.)
- **Loops**: Creates DJ-friendly loop segments (drum loops, vocal loops, melodic loops)
- **Color Name Comments**: Labels each cue with the musical elements present, making it easy to filter and find specific sounds when DJing

## Platform Support

**Mac only** - This script automatically finds your VirtualDJ database at:

```
~/Library/Application Support/VirtualDJ/database.xml
```

For Windows/Linux support, you would need to manually specify the database path.

## Limitations

Before using this script, be aware of these important requirements:

- **Files must be pre-analyzed**: The audio file needs to have already been analyzed by VirtualDJ (so it exists in the database)
- **Beat grid required**: The track must have a VirtualDJ beat grid. The script now checks the downbeat phase against audio/kick-stem transients and can correct a clearly shifted "1", but you should still manually review unusual tracks.
- **Close VirtualDJ before running**: Do not make any edits in VirtualDJ while the script is running, as changes to the database will cause your edits to be lost
- **Restart required**: You must close and reopen VirtualDJ after running the script for the cue points to appear
- **Platform compatibility**: Primarily tested on Mac. Windows and Linux compatibility is uncertain and may require additional configuration
- **Accuracy limitations**: The AI analysis is not always perfect. Always manually review and adjust the generated cue points. The default model is Gemini 3.1 Pro Preview, which may improve over earlier preview releases but can still have preview-model rate limits.
- **Long song limitations**: Really long songs (extended mixes, DJ sets) tend to have lower accuracy. The AI performs best on standard-length tracks (up to 6-7 minutes)

## Color System (My Personal DJ Preferences)

The colors reflect my DJing style and help me quickly find the right transition points:

- **Blue** - Melodic only (piano, strings, synth, guitar, bass) - NO drums or vocals

  - _Use case: Smooth ambient transitions, building tension_

- **Green** - Melodic + drums - NO vocals

  - _Use case: Instrumental breaks, building energy without lyrics_

- **Purple** - Drums only (80%+ drums/percussion)

  - _Use case: Perfect for transitions, drum breaks, mixing between tracks_

- **Yellow** - Full mix (drums + melody + vocals)

  - _Use case: Peak energy moments, main sections of tracks_

- **Orange** - Vocals + melody - NO drums
  - _Use case: Acapella sections, vocal-focused moments_

### Why Color-Coded Comments Matter

In VirtualDJ, you can **filter cues by color**. This means during a live set, I can:

- Quickly jump to "drums only" sections (purple) when I need a clean transition
- Find "melodic only" sections (blue) for smooth ambient mixing
- Locate "full mix" moments (yellow) for peak energy drops

The comments are automatically added to each cue describing the exact musical elements, making it easy to remember what each color means.

## Prerequisites

Before running the setup, make sure you have:

- **Python 3.9 or higher** - Check with `python3 --version`
- **VirtualDJ installed** - The script modifies the VirtualDJ database
- **VirtualDJ database created** - Run VirtualDJ at least once to create the database
- **Gemini API key** - Get one from [Google AI Studio](https://aistudio.google.com/app/apikey)

## Setup

Run the setup script to get started:

```bash
./setup.sh
```

The setup script will:

- Create a Python virtual environment (venv)
- Install all required dependencies
- Prompt you to enter your Gemini API key
- Create a `.env` file with your API key and default model

**Note**: This uses Python's built-in `venv` (virtual environment), not `pyenv`. No additional tools needed beyond Python 3.9+.

### Gemini Model

The default model is `gemini-3.1-pro-preview`. It is the newest Pro model currently listed in the Gemini API docs and supports audio input plus structured JSON output, but it is still a preview model. If it hits rate limits, switch to the latest stable Pro model, `gemini-2.5-pro`, or the newest stable Gemini model overall, `gemini-3.5-flash`.

```bash
python3 automatic_music_cuer_gemini.py --model gemini-2.5-pro "path/to/song.mp3"
```

You can also set `GEMINI_MODEL=gemini-2.5-pro` in `.env`.

## Usage

First, activate the virtual environment:

```bash
source venv/bin/activate
```

Then analyze your tracks:

```bash
# Analyze a single track (dry-run to preview changes)
python3 automatic_music_cuer_gemini.py --dry-run "path/to/song.mp3"

# Analyze and update VirtualDJ database
python3 automatic_music_cuer_gemini.py "path/to/song.mp3"

# Force a different Gemini model
python3 automatic_music_cuer_gemini.py --model gemini-3.5-flash "path/to/song.mp3"

# Process an entire folder (processes 5 songs at a time asynchronously)
python3 automatic_music_cuer_gemini.py "path/to/folder"
```

**Note**: When processing a folder, the script automatically handles multiple files and processes up to 5 songs concurrently for faster analysis.

### Visual Cue Audit

After cueing, generate a read-only visual audit report that overlays every cue on
the waveform and, when adjacent `.vdjstems` files exist, the separated drum,
vocal, bass, and instrument lanes:

```bash
python3 cue_visual_audit.py "path/to/folder" --output audit_reports/my-run
```

The report writes:

- `index.html` - links to one SVG report per track
- `all_cues.tsv` - every cue/loop with color, inferred elements, and energy shape
- `issues.tsv` - likely name/color mismatches and timing-shape review flags

Stem-backed name/color issues are the strongest signal. Tracks without
`.vdjstems` are limited to mixed-waveform placement review.

### Supported File Formats

The script handles all common audio formats including MP3, FLAC, WAV, and M4A. File sizes up to 200+ MB are supported, though extremely large files may take longer to upload and analyze.

## How It Works

1. **Stem Check**: Uses adjacent VirtualDJ `.vdjstems` files when available
2. **Upload**: Sends your audio file, and available VDJ stems, to Gemini AI
3. **Analysis**: Gemini listens to the entire track and identifies:
   - Musical elements (drums, bass, vocals, synth, piano)
   - Timing of transitions (when elements enter/exit)
   - Loop-friendly sections for DJing
4. **Stem Validation**: Corrects impossible labels/colors using stem activity, so a section is not marked drums-only if bass or instruments are active
5. **Beatgrid Verification**: Uses VDJ's BPM as the tempo prior, scores the stored grid against onset energy, applies fine offset correction only with strong kick-stem confidence, and falls back to multi-source bar-phase consensus when the kick stem is silent
6. **Color Assignment**: Based on detected elements, assigns colors according to the system above
7. **Database Update**: Safely writes cue points to your VirtualDJ database
8. **Backup**: Automatically creates timestamped backups before any changes

## What Gets Created

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

## Output Format

Each cue includes:

- **Timestamp**: Precise timing (rounded to 0.01s)
- **Name**: Descriptive label (e.g., "Drums In", "Vocal Drop")
- **Color**: Based on musical elements present
- **Comment**: Lists detected elements (e.g., "drums, bass, synth")

## VirtualDJ Integration

After running the script:

1. **Close and reopen VirtualDJ**: You must fully quit and restart VirtualDJ for the cue points to appear

2. **View your cues**:

   - Open the track in VirtualDJ
   - Cues appear in the waveform with assigned colors
   - Hover over cues to see comments

3. **Filter by color**:
   - Use VirtualDJ's cue filter to show only specific colors
   - Perfect for finding transition points during live sets

## Safety Features

- **Automatic Backups**: Creates timestamped backup before every change
- **Dry-Run Mode**: Preview changes without modifying database
- **VirtualDJ Process Guard**: Refuses real database writes while VirtualDJ is running
- **Beatgrid Downbeat Check**: Verifies cue snapping against audio transients and persists confident fine-offset or whole-beat downbeat corrections
- **XML Validation**: Validates database integrity before saving
- **Atomic Writes**: Uses temporary files to prevent corruption
- **Retry Logic**: Handles network errors gracefully with automatic retries

## Troubleshooting

### "GEMINI_API_KEY not found"

- Ensure `.env` file exists in this repository directory
- Check that API key is correctly formatted: `GEMINI_API_KEY=AIza...`
- Check that `GEMINI_MODEL` is set to a model your API key can access, such as `gemini-3.1-pro-preview` or `gemini-2.5-pro`

### "Database not found"

- Verify VirtualDJ is installed and has been run at least once
- Check path: `~/Library/Application Support/VirtualDJ/database.xml`

### "Upload failed" or "SSL errors"

- Check internet connection
- Script will automatically retry up to 5 times
- Large files may take longer to upload

### Cues not appearing in VirtualDJ

- Close and reopen

## Example Output

```
Automatic Music Cuer initialized with Gemini
VDJ Database: ~/Library/Application Support/VirtualDJ/database.xml
Database backed up to: database.xml.backup.20250124_143022
Analyzing song.mp3 with Gemini...
Uploading audio file (8.2 MB)...
Upload complete
Analyzing audio with Gemini...
Analysis complete: 6 cues, 3 loops

Cue 1: Intro at 0.00s - [synth] - Color: blue
Cue 2: Drums In at 45.23s - [drums, synth] - Color: green
Cue 3: Vocal Drop at 92.15s - [drums, vocals, synth] - Color: yellow
...

Successfully updated VDJ database with cues and loops
```

## Customization

To modify the color system for your preferences, edit the `color_mappings` dictionary in [automatic_music_cuer_gemini.py](automatic_music_cuer_gemini.py):

```python
self.color_mappings = {
    "blue": "4278190335",    # Your custom color
    "green": "4278255360",   # Your custom color
    ...
}
```

And update the color rules in the Gemini prompt.
