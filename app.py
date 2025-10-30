import os
import spotipy
import io
import csv
from spotipy.oauth2 import SpotifyOAuth
from flask import Flask, session, request, redirect, url_for, render_template, Response
from ytmusicapi import YTMusic

# Create the web app
app = Flask(__name__)

# --- App Configuration ---
app.secret_key = os.environ.get('FLASK_SECRET_KEY')
app.config['SESSION_COOKIE_HTTPONLY'] = True
SCOPE = 'playlist-read-private'
REDIRECT_URI = 'http://127.0.0.1:5000/callback'

# Set up the Spotify OAuth 2.0 helper
sp_oauth = SpotifyOAuth(scope=SCOPE, redirect_uri=REDIRECT_URI)

# Set up the YouTube Music client
# This doesn't need auth for public searching
ytmusic = YTMusic()


# --- Helper Function ---

def get_token():
    """
    Helper function to check for and refresh the access token.
    Returns the token_info dictionary or None.
    """
    token_info = session.get('token_info', None)
    
    if not token_info:
        # Not logged in
        return None

    # If token is expired, refresh it
    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
        session['token_info'] = token_info
        
    return token_info

# --- Web Page Routes ---

@app.route('/')
def index():
    """
    The homepage.
    Checks if a user is logged in.
    If not, shows 'index.html' (login page).
    If yes, fetches playlists and shows 'playlists.html'.
    """
    token_info = get_token()
    if not token_info:
        return render_template('index.html')

    try:
        sp = spotipy.Spotify(auth=token_info['access_token'])
        playlist_data = sp.current_user_playlists()
        playlists = playlist_data.get('items', [])
        return render_template('playlists.html', playlists=playlists)

    except spotipy.exceptions.SpotifyException as e:
        print(f"User's token is invalid: {e}")
        session.clear()
        return redirect(url_for('index'))


@app.route('/login')
def login():
    """Redirects user to Spotify login page."""
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)


@app.route('/logout')
def logout():
    """Logs the user out by clearing the session."""
    session.clear()
    return redirect(url_for('index'))


@app.route('/callback')
def callback():
    """The page Spotify sends the user to after they log in."""
    code = request.args.get('code')
    token_info = sp_oauth.get_access_token(code)
    session['token_info'] = token_info
    return redirect(url_for('index'))


# --- THIS IS THE NEW CONVERSION ROUTE ---

@app.route('/convert/<playlist_id>')
def convert_playlist(playlist_id):
    """
    The main logic. Fetches, searches, and converts the playlist.
    """
    # 1. Check for login
    token_info = get_token()
    if not token_info:
        return redirect(url_for('index'))
    
    # 2. Get the desired format (m3u or csv) from the URL
    file_format = request.args.get('format', 'm3u')
    
    try:
        # 3. Create Spotify client
        sp = spotipy.Spotify(auth=token_info['access_token'])
        
        # 4. Get playlist name for the filename
        playlist_info = sp.playlist(playlist_id, fields='name')
        # Sanitize filename (remove characters that are bad for filenames)
        safe_name = "".join(c for c in playlist_info['name'] if c.isalnum() or c in (' ', '_')).rstrip()
        filename = f"{safe_name}.{file_format}"

        # 5. Fetch ALL tracks from the playlist (handling pagination)
        print(f"Fetching tracks for playlist: {playlist_info['name']}")
        spotify_tracks = []
        results = sp.playlist_tracks(playlist_id)
        spotify_tracks.extend(results['items'])
        
        # The 'sp.next(results)' function automatically handles pagination
        while results['next']:
            results = sp.next(results)
            spotify_tracks.extend(results['items'])
        
        # 6. Convert tracks
        print(f"Converting {len(spotify_tracks)} tracks...")
        converted_tracks = []
        for item in spotify_tracks:
            if not item.get('track'):
                continue # Skip if track is unavailable (e.g., local file)

            track = item['track']
            spotify_title = track['name']
            spotify_artists = ", ".join([a['name'] for a in track['artists']])
            spotify_duration_sec = track['duration_ms'] // 1000
            
            # Create a search query for YouTube Music
            search_query = f"{spotify_title} {spotify_artists}"
            
            # Search YouTube Music
            # We filter by 'songs' and take the top result
            search_results = ytmusic.search(search_query, filter='songs')
            
            if not search_results:
                print(f"  [!] No match found for: {search_query}")
                continue
                
            # Get the Video ID from the top search result
            yt_video_id = search_results[0]['videoId']
            
            converted_tracks.append({
                'title': spotify_title,
                'artists': spotify_artists,
                'duration': spotify_duration_sec,
                'media_id': yt_video_id
            })
        
        print(f"Successfully converted {len(converted_tracks)} tracks.")

        # 7. Generate and send the file
        
        # We use io.StringIO to build the file in memory
        si = io.StringIO()
        
        if file_format == 'm3u':
            si.write("#EXTM3U\n")
            for track in converted_tracks:
                # Format: #EXTINF:Duration,Artist - Title
                si.write(f"#EXTINF:{track['duration']},{track['artists']} - {track['title']}\n")
                si.write(f"https://youtube.com/watch?v={track['media_id']}\n")
            
            mimetype = 'audio/x-mpegurl'
        
        elif file_format == 'csv':
            # Write CSV header based on your 'English.csv'
            header = ['PlaylistBrowseId', 'PlaylistName', 'MediaId', 'Title', 'Artists', 'Duration', 'ThumbnailUrl']
            writer = csv.writer(si)
            writer.writerow(header)
            
            for track in converted_tracks:
                writer.writerow([
                    playlist_id,          # PlaylistBrowseId
                    playlist_info['name'],# PlaylistName
                    track['media_id'],    # MediaId
                    track['title'],       # Title
                    track['artists'],     # Artists
                    track['duration'],    # Duration
                    ''                    # ThumbnailUrl (we don't have this)
                ])
            
            mimetype = 'text/csv'

        else:
            return "Invalid format", 400

        # Get the full string from the StringIO object
        output = si.getvalue()
        
        # 8. Send the file to the user as a download
        return Response(
            output,
            mimetype=mimetype,
            headers={"Content-Disposition": f"attachment;filename={filename}"}
        )

    except Exception as e:
        print(f"An error occurred during conversion: {e}")
        # If something fails, send the user back home
        return redirect(url_for('index'))


# --- Run the App ---
if __name__ == '__main__':
    app.run(debug=True, port=5000)
