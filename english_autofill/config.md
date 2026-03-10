# English Auto-fill — Configuration

## deepl_api_key
Your DeepL API authentication key for Russian translation.

Get a **free** key (500,000 characters/month) at https://www.deepl.com/pro-api

Leave empty to skip automatic translation.

## deepl_free_tier
`true`  — use the DeepL **Free** API endpoint (api-free.deepl.com).
`false` — use the DeepL **Pro** API endpoint (api.deepl.com).

Change to `false` only if you have a paid DeepL Pro subscription.

## unsplash_access_key
Your Unsplash API **Access Key** for image suggestions.

Get a free key (50 requests/hour) at https://unsplash.com/developers

Leave empty to disable image suggestions.

## pixabay_api_key
Your Pixabay API key — used as a fallback when `image_provider` is set to
`"pixabay"` or when the Unsplash key is missing.

Get a free key at https://pixabay.com/api/docs/

## image_provider
Which image service to query first.
`"unsplash"` (default) or `"pixabay"`.

The add-on automatically falls back to the other provider if the primary key
is missing.

## max_examples
Maximum number of example sentences to include in the Examples field.
Default: `3`.

## cloze_number
The cloze deletion number used in the Examples field (`c1`, `c2`, …).
Default: `1` — produces `{{c1::word}}`.

## target_note_type
The exact name of the Anki note type this add-on operates on.
Default: `"English expression (Cloze)"`.

The Auto-fill button silently does nothing if the current note type doesn't
match this value.

## overwrite_existing_fields
`true`  — overwrite fields that already contain content (default).
`false` — skip fields that already have content (safe for partial re-runs).
