2026-09-05
- cut function for movies
- 



---= DONE =---

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
