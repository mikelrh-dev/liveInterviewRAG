# Mobile Audio Recording Specification

## Purpose

Enable end-to-end voice recording and transcription on iOS Safari and Android Chrome by selecting a supported MediaRecorder codec, deriving correct upload metadata, and providing distinct error messages for codec and permission failures.

## Requirements

### Requirement: Codec Fallback Chain

The system SHALL select the first supported MIME type from the ordered chain `[audio/webm;codecs=opus, audio/webm, audio/mp4;codecs=mp4a.40.2, audio/mp4]` using `MediaRecorder.isTypeSupported()`. The selected MIME type SHALL be used for both the Blob type and the filename extension.

#### Scenario: iOS Safari records via mp4

- GIVEN a user opens the app on iPhone Safari ≥15
- WHEN the user taps the mic and records a question
- THEN the codec selection resolves to `audio/mp4` (or `audio/mp4;codecs=mp4a.40.2`)
- AND the uploaded file is named `recording.m4a` with content-type `audio/mp4`

#### Scenario: Android Chrome keeps webm

- GIVEN a user opens the app on Android Chrome
- WHEN the user taps the mic and records a question
- THEN the codec selection resolves to `audio/webm;codecs=opus`
- AND the uploaded file is named `recording.webm` with content-type `audio/webm`
- AND no regression occurs versus current desktop behavior

#### Scenario: Desktop Chrome unchanged

- GIVEN a user opens the app on desktop Chrome
- WHEN the user records audio
- THEN the codec selection resolves to `audio/webm;codecs=opus`
- AND behavior is identical to the pre-change state

### Requirement: Backend Accepts mp4 Uploads

The system SHALL accept `audio/mp4` and `.m4a` file uploads alongside existing `audio/webm` and `.webm` uploads. The transcription pipeline SHALL remain unchanged — ffmpeg handles both containers transparently.

#### Scenario: mp4 upload accepted by send-message endpoint

- GIVEN the frontend uploads an `audio/mp4` blob as `recording.m4a`
- WHEN the backend receives the upload at `/api/conversation/{id}/message`
- THEN the upload is accepted (HTTP 200)
- AND the temp file is saved with a `.m4a` extension matching the content type

#### Scenario: mp4 upload accepted by stream endpoint

- GIVEN the frontend uploads an `audio/mp4` blob as `recording.m4a`
- WHEN the backend receives the upload at `/api/conversation/{id}/message/stream`
- THEN the upload is accepted (HTTP 200)
- AND transcription proceeds without error

### Requirement: Distinct Error Messages

The system SHALL display a user-facing error message that distinguishes between a microphone permission denial and an unsupported codec. The messages SHALL NOT be identical or generic.

#### Scenario: Permission denied error

- GIVEN the user denies microphone permission
- WHEN recording is attempted
- THEN the error message mentions microphone permission (e.g., "Microphone access denied")

#### Scenario: Unsupported codec error

- GIVEN a browser where no codec in the fallback chain is supported
- WHEN recording is attempted
- THEN the error message mentions codec/browser compatibility (e.g., "Recording not supported in this browser")
- AND the message does NOT mention microphone permission
