import { describe, expect, it, vi } from 'vitest'
import { ConfigError, loadConfig, normalizeBasePath, parseConfig } from './config'

const valid = {
  apiBaseUrl: 'https://corvus.example.org/api/v1/',
  basePath: 'corvus',
  auth: { mode: 'oidc', rolesClaim: 'realm_access.roles', roleMapping: { 'corvus-admin': 'admin' } },
  oidc: { issuer: 'https://sso.example.org/realms/olf', clientId: 'corvus-ui', scopes: 'openid profile roles' },
}

const response = (body: unknown, init: ResponseInit = {}) => new Response(JSON.stringify(body), init)

describe('parseConfig', () => {
  it('accepts a complete OIDC config and trims the API trailing slash', () => {
    const cfg = parseConfig(valid)
    expect(cfg.apiBaseUrl).toBe('https://corvus.example.org/api/v1')
    expect(cfg.oidc.clientId).toBe('corvus-ui')
    expect(cfg.auth.roleMapping).toEqual({ 'corvus-admin': 'admin' })
  })

  it('normalizes the base path to leading and trailing slash', () => {
    expect(parseConfig(valid).basePath).toBe('/corvus/')
    expect(normalizeBasePath('/')).toBe('/')
    expect(normalizeBasePath('//a//b')).toBe('/a/b/')
  })

  it('defaults to the root path, oidc mode and the roles claim', () => {
    const cfg = parseConfig({ apiBaseUrl: '/api/v1', oidc: { issuer: 'https://i', clientId: 'c' } })
    expect(cfg.basePath).toBe('/')
    expect(cfg.auth.mode).toBe('oidc')
    expect(cfg.auth.rolesClaim).toBe('roles')
    expect(cfg.oidc.scopes).toBe('openid profile')
  })

  it('does not require OIDC settings in token mode', () => {
    expect(parseConfig({ apiBaseUrl: '/api/v1', auth: { mode: 'token' } }).auth.mode).toBe('token')
  })

  it.each([
    ['not an object', 'x', /JSON object/],
    ['missing apiBaseUrl', { oidc: valid.oidc }, /apiBaseUrl/],
    ['relative apiBaseUrl', { ...valid, apiBaseUrl: 'api/v1' }, /absolute/],
    ['unknown auth mode', { ...valid, auth: { mode: 'basic' } }, /auth\.mode/],
    ['missing issuer', { apiBaseUrl: '/api/v1', oidc: { clientId: 'c' } }, /oidc\.issuer/],
    ['non-string clientId', { apiBaseUrl: '/api/v1', oidc: { issuer: 'https://i', clientId: 5 } }, /clientId/],
  ])('rejects %s', (_name, input, message) => {
    expect(() => parseConfig(input)).toThrow(ConfigError)
    expect(() => parseConfig(input)).toThrow(message)
  })
})

describe('loadConfig', () => {
  it('fetches the URL without caching and validates the body', async () => {
    const fetchFn = vi.fn().mockResolvedValue(response(valid))
    const cfg = await loadConfig(fetchFn, '/corvus/config.json')
    expect(fetchFn).toHaveBeenCalledWith('/corvus/config.json', expect.objectContaining({ cache: 'no-store' }))
    expect(cfg.basePath).toBe('/corvus/')
  })

  it('reports an HTTP error', async () => {
    const fetchFn = vi.fn().mockResolvedValue(response({}, { status: 404 }))
    await expect(loadConfig(fetchFn, '/config.json')).rejects.toThrow(/HTTP 404/)
  })

  it('reports an unreachable file', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new TypeError('network'))
    await expect(loadConfig(fetchFn, '/config.json')).rejects.toThrow(/could not be loaded/)
  })

  it('reports invalid JSON', async () => {
    const fetchFn = vi.fn().mockResolvedValue(new Response('{nope', { status: 200 }))
    await expect(loadConfig(fetchFn, '/config.json')).rejects.toThrow(/not valid JSON/)
  })
})
