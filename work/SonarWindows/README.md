# Sonar for Windows

Control a Windows PC with a wave of your hand. This port uses the computer’s speakers and microphone to detect movement.

**This is an experiment in progress.** The original app is native macOS. This directory is a Windows implementation of the same Doppler sensing, gestures, and practice UI.

## Run

Install Python 3.11 or later, then:

```
python -m pip install -r work/SonarWindows/requirements.txt
python work/SonarWindows/run_sonar.py
```

Run the synthetic tests without starting audio:

```
python work/SonarWindows/run_sonar.py --self-test
```

Allow microphone access. Use the built-in speakers and microphone. Stop if the tone is audible or uncomfortable.

## Gestures

- Scroll: lift your hand to scroll; lower it to reset. Two short downward pushes switch direction when Air double-tap is on.
- Swipe: sweep sideways to change photos. Pause before returning your hand. Reverse directions swaps the mapping.
- Zoom: push toward the screen to zoom in and pull back to zoom out. Other apps receive Ctrl plus/minus; browsers reset with Ctrl+0.
- Signal, Distance, and Position are experimental views.

Practice here stays in the Sonar window. Turn on **Control other apps** to send scroll-wheel motion, left/right arrows, or Ctrl plus/minus to the foreground window. Click the target content first.

Stop the session from the window, with Escape, or with Ctrl+Alt+Win+Space.

Windows cannot match macOS Accessibility field checks. Other-app control sends input even when a text field is focused, so keep that option off while typing.

## How it works

Speakers emit a steady tone (20 kHz by default). Sound reflects off a moving hand. Motion toward the audio hardware raises the reflected frequency and motion away lowers it. The microphone receives the reflections. The app detects frequency-change patterns and maps recognized gestures to commands.

Accuracy testing is ongoing. Position estimates remain experimental.

## Pets

The default tone is 20 kHz. Dogs and cats can hear this frequency. Use Sonar away from pets and stop if they seem uncomfortable. Pet safety and sound levels still need evaluation.

Hearing-range source: https://www.lsu.edu/vetmed/deafness/hearingrange.php
