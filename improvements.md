2026-09-05
- add cut and crop function for movies (same functionality as for the soundtracks)
- add possibility to enter a default time for all slides (put it net to "transition default")
- show transition symbol also in the detailed slide list (same as storyline)
- in detailed slide list make box for effect smaller (fit to largest text)
- advise on better gui for the selection of the transition in the detailed slide list as the list is now too long
- make new popup window to show small examples of all possible transitions. remember / save the examples so you only need to generate them 1 time. give options for gui
- move the complete pane with "PHOTO SELECTION - All photos" and "TRANSITION DEFAULT" just above the storyline (instead of below the storyline)
- include "random text transition" button in "PHOTO SELECTION - All photos" pane
- in project header near the "load project", "clear all", "save project" buttons give also indication if generation is ongoing (including progress and estimated time to finish)
- in generation and output pane put "estimated time to generate", estimated file size, ESTIMATED TOTAL SLIDESHOW TIME
- Transition preview popup do not show the "quick preview", only the "ACCURATE FFMPEG · 360P", and only show the transition (so if transition duration is 5 sec, the example clip should be 5 seconds)
- also give different symbols for the GL transitions.
- for the time ruler indicate the time in hour:minutes:seconds (h:mm:ss) instead of only seconds.
- on the picture preview popup move the next / previous buttons inside the picture and make the active area bigger (complete right side of picture: go to next; left side of the picture: go to previous)
- on the picture preview popup add options to edit / change the pictures by adding effect and filters (https://www.photofilters.com/), for example: make picture black and white;
- on the picture preview popup add options to cut and crop parts of the picture
- in output pane the filename should be the same as the project name on the top (and visa versa)
- "load project" and "save project" button should call up browse popup so user can select path and filename where to save/load
- 



---= DONE =---

2026-09-05
- add favicon
- add enable/disable function for text on picture. advise on gui options for storyline and list with photos (see docs/text-on-picture-toggle.md)

2026-09-04
- in SAVED IN SQLITE popup, provide a button per entry to delete that entry, also 1 button to delete all.
- advise if we can use gt transitions (https://github.com/scriptituk/xfade-easing#ported-glsl-transitions and https://gl-transitions.com/) to add more transitions. first give options how to implement and what are the different options. goal is to split the final transitions in 2 main groups: 1- based on xfade (these are the current transitions, no change) and 2- GL transitions (these new transitions). I like to have as many transitions as possible (for both groups), so also check if we already have all xfade transitions (give an overview, before implementing the new ones).

2026-09-03
- in photo preview popup window provide functionality to rotate the photo +90 and -90 degrees. this new orientation should also be used in final slideshow result
- in audio preview add possibility to fast forward / reverse the audio track, preferable with drag and drop on a time bar. give options before implementation
- at the end of the slideshow (last photo) make sure the audio is fade out. fade out time to be set by the user via soundtracks pane
- for the individual soundtracks add possibility that user can cut and crop the audio file and add options to fade in and fade out. soundtrack edit to be done in a new popup window.
  keep in mind that total estimate time for tha audio will only user the real audio time and not the total track time. give options for gui and to call up the new popup.
- improvement for Text frame editor: for the background colour on the text frames add possibility to define 2 different colours and to use the existing transitions to go from the first to the second colour. give options for gui.
- add more different fonts for the default text style and text frame. give options and advise which fonts to add.
- in the main header between "storyline" and "soundtrack" add "transitions" to jump to the bottom of the storyline
- add option to normalize the overall audio output in case there are multiple soundtrack but they do not have the same output level. give options for gui.
- disable ken burn effect by default 
- cancel of adding a text frame still adds a text frame, this should be only by save
- add possibility to quickly push a selected photo to a specific place in the storyline. give option for gui.
