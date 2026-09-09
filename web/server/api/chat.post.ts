import { timingSafeEqual } from 'node:crypto'

interface ChatRequestBody {
  question: string
  history?: Array<[string, string]>
}

// Constant-time comparison, guarding the length mismatch node's own
// timingSafeEqual throws on instead of returning false for — see
// bsbot's hmac.compare_digest usage on the Python side for the same idea.
function safeEqual(a: string, b: string): boolean {
  const bufA = Buffer.from(a)
  const bufB = Buffer.from(b)
  if (bufA.length !== bufB.length) return false
  return timingSafeEqual(bufA, bufB)
}

// The actual token validation ("backend api part of the Nuxt app" — spec
// bsbot-matrix-chatbot#014). The frontend never checks the token itself; it
// only finds out a token was wrong via a 401 from here.
export default defineEventHandler(async (event): Promise<AskResponse> => {
  const config = useRuntimeConfig(event)

  if (!config.chatAccessToken) {
    // Fail closed: an unset token must never mean "no token required".
    throw createError({ statusCode: 500, statusMessage: 'chat access token not configured' })
  }

  const cookieToken = getCookie(event, 'bs_chat_token') ?? ''
  if (!cookieToken || !safeEqual(cookieToken, config.chatAccessToken)) {
    throw createError({ statusCode: 401, statusMessage: 'invalid or missing token' })
  }

  const body = await readBody<ChatRequestBody>(event)
  if (!body?.question?.trim()) {
    throw createError({ statusCode: 400, statusMessage: 'question is required' })
  }

  try {
    return await $fetch<AskResponse>(`${config.bsbotApiUrl}/api/ask`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${config.bsbotApiToken}` },
      body: { question: body.question, history: body.history ?? null }
    })
  } catch (error) {
    const status = (error as { response?: { status?: number } })?.response?.status ?? 502
    throw createError({ statusCode: status, statusMessage: 'bsbot api request failed' })
  }
})
