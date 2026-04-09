param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("list-models", "upload-image", "generate-prompts", "submit-video", "poll-video")]
    [string]$Action,

    [string]$BaseUrl = "",
    [string]$ApiKey = "",
    [string]$Model = "",
    [string]$ImagePath = "",
    [string]$ImageUrl = "",
    [string]$Prompt = "",
    [string]$ProductName = "",
    [string]$Platform = "douyin",
    [string]$TargetMarket = "",
    [string]$AnalysisModel = "gemini-2.5-pro",
    [string]$AspectRatio = "9:16",
    [bool]$EnhancePrompt = $true,
    [string]$TaskId = "",
    [int]$PollIntervalSeconds = 12,
    [int]$MaxPollCount = 50
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http

function Resolve-Setting {
    param(
        [string]$ExplicitValue,
        [string]$EnvName,
        [string]$DefaultValue = ""
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitValue)) {
        return $ExplicitValue
    }

    $envValue = [Environment]::GetEnvironmentVariable($EnvName)
    if (-not [string]::IsNullOrWhiteSpace($envValue)) {
        return $envValue
    }

    return $DefaultValue
}

$BaseUrl = (Resolve-Setting -ExplicitValue $BaseUrl -EnvName "COMFLY_API_BASE" -DefaultValue "https://ai.comfly.chat").TrimEnd("/")
$ApiKey = Resolve-Setting -ExplicitValue $ApiKey -EnvName "COMFLY_API_KEY"
$ConfiguredVideoModel = Resolve-Setting -ExplicitValue $Model -EnvName "COMFLY_VIDEO_MODEL" -DefaultValue "veo3.1-fast"

if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    throw "Missing API key. Set COMFLY_API_KEY or pass -ApiKey."
}

function New-HttpClient {
    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds(180)
    $client.DefaultRequestHeaders.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $ApiKey)
    $client.DefaultRequestHeaders.Accept.Add([System.Net.Http.Headers.MediaTypeWithQualityHeaderValue]::new("application/json"))
    return $client
}

function Invoke-JsonApi {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Url,
        [object]$Body
    )

    $client = New-HttpClient
    try {
        $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::$Method, $Url)
        if ($null -ne $Body) {
            $json = $Body | ConvertTo-Json -Depth 20
            $request.Content = [System.Net.Http.StringContent]::new($json, [System.Text.Encoding]::UTF8, "application/json")
        }

        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()

        if (-not $response.IsSuccessStatusCode) {
            throw "HTTP $([int]$response.StatusCode) $($response.ReasonPhrase): $content"
        }

        if ([string]::IsNullOrWhiteSpace($content)) {
            return $null
        }

        return $content | ConvertFrom-Json -Depth 50
    }
    finally {
        $client.Dispose()
    }
}

function Invoke-MultipartApi {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [hashtable]$Fields,
        [string]$FileField,
        [string]$FilePath
    )

    $client = New-HttpClient
    $fileStream = $null

    try {
        $content = [System.Net.Http.MultipartFormDataContent]::new()

        if ($Fields) {
            foreach ($key in $Fields.Keys) {
                $value = [string]$Fields[$key]
                $content.Add([System.Net.Http.StringContent]::new($value, [System.Text.Encoding]::UTF8), $key)
            }
        }

        if (-not [string]::IsNullOrWhiteSpace($FileField) -and -not [string]::IsNullOrWhiteSpace($FilePath)) {
            $resolvedPath = (Resolve-Path -LiteralPath $FilePath).Path
            $fileStream = [System.IO.File]::OpenRead($resolvedPath)
            $streamContent = [System.Net.Http.StreamContent]::new($fileStream)
            $streamContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("application/octet-stream")
            $content.Add($streamContent, $FileField, [System.IO.Path]::GetFileName($resolvedPath))
        }

        $response = $client.PostAsync($Url, $content).GetAwaiter().GetResult()
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()

        if (-not $response.IsSuccessStatusCode) {
            throw "HTTP $([int]$response.StatusCode) $($response.ReasonPhrase): $body"
        }

        return $body | ConvertFrom-Json -Depth 50
    }
    finally {
        if ($fileStream) {
            $fileStream.Dispose()
        }
        $client.Dispose()
    }
}

function Get-UploadedImageUrl {
    param([string]$LocalImagePath)

    if ([string]::IsNullOrWhiteSpace($LocalImagePath)) {
        throw "Image path is required."
    }

    return Invoke-MultipartApi -Url "$BaseUrl/v1/files" -Fields @{} -FileField "file" -FilePath $LocalImagePath
}

function Get-JsonFromModelText {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        throw "Model response was empty."
    }

    $candidate = $Text.Trim()
    $fence = '```json'
    $start = $candidate.IndexOf($fence)
    if ($start -ge 0) {
        $jsonStart = $start + $fence.Length
        $end = $candidate.IndexOf('```', $jsonStart)
        if ($end -gt $jsonStart) {
            $candidate = $candidate.Substring($jsonStart, $end - $jsonStart).Trim()
        }
    }

    try {
        return $candidate | ConvertFrom-Json -Depth 50
    }
    catch {
        $clean = $candidate.Replace('```json', '').Replace('```', '').Trim()
        $clean = [regex]::Replace($clean, ',\s*}', '}')
        $clean = [regex]::Replace($clean, ',\s*]', ']')

        try {
            return $clean | ConvertFrom-Json -Depth 50
        }
        catch {
            $fallback = @()
            $index = 1
            foreach ($line in ($Text -split "`n")) {
                $trimmed = ($line -replace "^\s*[-*\d\.\)]\s*", "").Trim()
                if ($trimmed) {
                    $fallback += [pscustomobject]@{
                        title = "Prompt $index"
                        video_prompt = $trimmed
                    }
                    $index++
                }
                if ($fallback.Count -ge 5) {
                    break
                }
            }
            return $fallback
        }
    }
}

switch ($Action) {
    "list-models" {
        [pscustomobject]@{
            base_url = $BaseUrl
            documented_google_veo_models = @(
                "veo3.1-fast",
                "veo3.1",
                "veo3.1-pro",
                "veo3.1-components",
                "veo3-pro-frames",
                "veo3-fast-frames",
                "veo2-fast-frames",
                "veo2-fast-components"
            )
            notes = @(
                "Current browser-visible docs show the Google-Veo models above.",
                "The user confirmed veo3.1-fast is the actually usable runtime model in this environment.",
                "The docs page showed veo3.1, not veo3.1-fast.",
                "Use v2/videos/generations for submit and v2/videos/generations/{task_id} for polling."
            )
        } | ConvertTo-Json -Depth 20
        break
    }

    "upload-image" {
        $upload = Get-UploadedImageUrl -LocalImagePath $ImagePath
        $upload | ConvertTo-Json -Depth 20
        break
    }

    "generate-prompts" {
        if ([string]::IsNullOrWhiteSpace($ImageUrl)) {
            $upload = Get-UploadedImageUrl -LocalImagePath $ImagePath
            $ImageUrl = $upload.url
        }

        if ([string]::IsNullOrWhiteSpace($ImageUrl)) {
            throw "Could not obtain image URL for prompt generation."
        }

        $productLine = if ($ProductName) { "Product name: $ProductName" } else { "Product name: not provided. Infer the product type from the image." }
        $marketLine = if ($TargetMarket) { "Target audience: $TargetMarket" } else { "Target audience: short-form ecommerce shoppers." }

        $analysisPromptLines = @(
            "You are an expert Chinese short-form ecommerce video director and prompt writer.",
            "",
            "Based on the user product image, generate 5 distinct Chinese ecommerce selling prompts.",
            "Each prompt should be suitable for a vertical 9:16 product video on Douyin-style short video platforms.",
            "",
            "Requirements:",
            "1. Focus on real product display, close-up detail shots, use-case scenes, buying intent, and conversion.",
            "2. Do not include subtitles, stickers, interface overlays, or watermarks.",
            "3. Make each prompt specific and executable for a video model.",
            "4. Return strict JSON array only, no commentary.",
            "",
            $productLine,
            $marketLine,
            "",
            "Use this structure:",
            "[",
            "  {",
            "    ""title"": ""Style name in Chinese"",",
            "    ""video_prompt"": ""Complete Chinese selling prompt""",
            "  }",
            "]"
        )
        $analysisPrompt = [string]::Join([Environment]::NewLine, $analysisPromptLines)

        $body = @{
            model = $AnalysisModel
            stream = $false
            messages = @(
                @{
                    role = "user"
                    content = @(
                        @{
                            type = "text"
                            text = $analysisPrompt
                        },
                        @{
                            type = "image_url"
                            image_url = @{
                                url = $ImageUrl
                            }
                        }
                    )
                }
            )
            max_tokens = 4000
        }

        $result = Invoke-JsonApi -Method "Post" -Url "$BaseUrl/v1/chat/completions" -Body $body
        $content = $result.choices[0].message.content
        $prompts = Get-JsonFromModelText -Text $content

        [pscustomobject]@{
            image_url = $ImageUrl
            analysis_model = $AnalysisModel
            prompt_count = @($prompts).Count
            prompts = $prompts
            raw_text = $content
        } | ConvertTo-Json -Depth 30
        break
    }

    "submit-video" {
        $effectiveModel = if ($ConfiguredVideoModel) { $ConfiguredVideoModel } else { $Model }
        if ([string]::IsNullOrWhiteSpace($effectiveModel)) {
            throw "Missing video model. Pass -Model or set COMFLY_VIDEO_MODEL."
        }

        if ([string]::IsNullOrWhiteSpace($Prompt)) {
            throw "Missing prompt. Pass -Prompt."
        }

        if ([string]::IsNullOrWhiteSpace($ImageUrl) -and -not [string]::IsNullOrWhiteSpace($ImagePath)) {
            $upload = Get-UploadedImageUrl -LocalImagePath $ImagePath
            $ImageUrl = $upload.url
        }

        if ([string]::IsNullOrWhiteSpace($ImageUrl)) {
            throw "Missing image reference. Pass -ImageUrl or -ImagePath."
        }

        $requestData = @{
            prompt = $Prompt
            model = $effectiveModel
            images = @($ImageUrl)
        }

        if (-not [string]::IsNullOrWhiteSpace($AspectRatio)) {
            $requestData.aspect_ratio = $AspectRatio
        }

        if ($EnhancePrompt) {
            $requestData.enhance_prompt = $true
        }

        $result = Invoke-JsonApi -Method "Post" -Url "$BaseUrl/v2/videos/generations" -Body $requestData
        [pscustomobject]@{
            submitted = $true
            base_url = $BaseUrl
            model = $effectiveModel
            image_url = $ImageUrl
            request = $requestData
            task = $result
        } | ConvertTo-Json -Depth 30
        break
    }

    "poll-video" {
        if ([string]::IsNullOrWhiteSpace($TaskId)) {
            throw "Missing task ID. Pass -TaskId."
        }

        $history = @()
        for ($i = 1; $i -le $MaxPollCount; $i++) {
            $result = Invoke-JsonApi -Method "Get" -Url "$BaseUrl/v2/videos/generations/$TaskId"
            $mp4url = ""
            if ($result.data -and $result.data.output) {
                $mp4url = $result.data.output
            }

            $history += [pscustomobject]@{
                attempt = $i
                status = $result.status
                progress = $result.progress
                mp4url = $mp4url
                fail_reason = $result.fail_reason
                checked_at = [DateTimeOffset]::UtcNow.ToString("o")
            }

            if ($result.status -eq "SUCCESS" -and $mp4url) {
                [pscustomobject]@{
                    completed = $true
                    attempts = $i
                    task_id = $result.task_id
                    status = $result.status
                    progress = $result.progress
                    mp4url = $mp4url
                    raw = $result
                    history = $history
                } | ConvertTo-Json -Depth 30
                return
            }

            if ($result.status -eq "FAILURE") {
                [pscustomobject]@{
                    completed = $false
                    failed = $true
                    attempts = $i
                    task_id = $result.task_id
                    status = $result.status
                    fail_reason = $result.fail_reason
                    raw = $result
                    history = $history
                } | ConvertTo-Json -Depth 30
                return
            }

            Start-Sleep -Seconds $PollIntervalSeconds
        }

        [pscustomobject]@{
            completed = $false
            timed_out = $true
            attempts = $MaxPollCount
            task_id = $TaskId
            history = $history
        } | ConvertTo-Json -Depth 30
        break
    }
}
