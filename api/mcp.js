// An MCP server over the research findings.
//
// The brief asked for a deliverable "easy for both an agent and a human to
// consume". A human gets index.html. An agent gets this: the same dataset as
// callable tools, so a reviewer can point Claude Code or Cursor at the URL and
// interrogate the findings instead of reading them.
//
//   claude mcp add --transport http toolkit-radar https://<host>/api/mcp
//
// Streamable HTTP transport, JSON-RPC 2.0, no dependencies. Data is read from
// the deployed /data.json so the tools can never drift from the page.

const PROTOCOL_VERSION = '2025-06-18';
const SERVER = { name: 'toolkit-radar', version: '1.0.0' };

let cache = null;

async function load(req) {
  if (cache) return cache;
  const host = req.headers['x-forwarded-host'] || req.headers.host;
  const proto = req.headers['x-forwarded-proto'] || 'https';
  const res = await fetch(`${proto}://${host}/data.json`);
  if (!res.ok) throw new Error(`could not load data.json: ${res.status}`);
  cache = await res.json();
  return cache;
}

const SLIM = [
  'id', 'app', 'category', 'one_liner', 'primary_auth', 'auth_methods', 'access',
  'access_note', 'api_surface', 'api_breadth', 'has_mcp', 'verdict', 'blocker',
  'build_score', 'effort', 'lane', 'in_composio', 'composio_slug', 'evidence',
];

const slim = (r) => Object.fromEntries(SLIM.filter((k) => k in r).map((k) => [k, r[k]]));

const TOOLS = [
  {
    name: 'search_apps',
    description:
      'Filter the 100 researched apps. Every argument is optional and they AND together. ' +
      'Use this to answer questions like "which fintech apps are self-serve" or ' +
      '"what is gated behind a partnership".',
    inputSchema: {
      type: 'object',
      properties: {
        category: { type: 'string', description: 'Substring match, e.g. "Finance", "Ecommerce".' },
        access: {
          type: 'string',
          enum: ['self_serve_free', 'self_serve_paid', 'plan_gated', 'approval_required', 'partner_gated', 'no_public_api', 'unknown'],
        },
        primary_auth: { type: 'string', description: 'e.g. OAUTH2, API_KEY, BASIC, NO_AUTH.' },
        lane: { type: 'string', enum: ['build_now', 'quick_win', 'needs_outreach', 'park'] },
        has_mcp: { type: 'boolean', description: 'Vendor-published MCP server exists.' },
        in_composio: { type: 'boolean', description: 'Already a Composio toolkit.' },
        limit: { type: 'integer', default: 25 },
      },
    },
  },
  {
    name: 'get_app',
    description: 'Everything known about one app, including its evidence URLs, the critic audit, and any browser gate check.',
    inputSchema: {
      type: 'object',
      properties: { name: { type: 'string', description: 'App name, case-insensitive substring.' } },
      required: ['name'],
    },
  },
  {
    name: 'build_queue',
    description:
      'The ranked list of apps Composio does NOT ship yet, highest build score first, ' +
      'with the score breakdown. This is the roadmap answer, not the raw table.',
    inputSchema: {
      type: 'object',
      properties: {
        lane: { type: 'string', enum: ['build_now', 'quick_win', 'needs_outreach', 'park'] },
        limit: { type: 'integer', default: 15 },
      },
    },
  },
  {
    name: 'patterns',
    description: 'The cross-cutting distributions: auth mix, access mix, MCP adoption, per-category matrix, Composio coverage.',
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'verification',
    description:
      'How trustworthy these findings are: closed-book baseline accuracy, grounded accuracy, ' +
      'the per-field breakdown, every miss against the gold set, and agreement with Composio\'s own catalog.',
    inputSchema: {
      type: 'object',
      properties: { include_misses: { type: 'boolean', default: true } },
    },
  },
];

async function call(name, args, req) {
  const d = await load(req);
  const rows = d.rows || [];

  switch (name) {
    case 'search_apps': {
      const a = args || {};
      let out = rows.filter((r) =>
        (a.category === undefined || (r.category || '').toLowerCase().includes(String(a.category).toLowerCase())) &&
        (a.access === undefined || r.access === a.access) &&
        (a.primary_auth === undefined || r.primary_auth === a.primary_auth) &&
        (a.lane === undefined || r.lane === a.lane) &&
        (a.has_mcp === undefined || r.has_mcp === a.has_mcp) &&
        (a.in_composio === undefined || r.in_composio === a.in_composio));
      const total = out.length;
      out = out.sort((x, y) => y.build_score - x.build_score).slice(0, a.limit ?? 25);
      return { matched: total, returned: out.length, apps: out.map(slim) };
    }

    case 'get_app': {
      const q = String((args || {}).name || '').toLowerCase();
      const hit = rows.find((r) => r.app.toLowerCase() === q) ||
                  rows.find((r) => r.app.toLowerCase().includes(q));
      if (!hit) {
        return { error: `no app matching "${q}"`, did_you_mean: rows.map((r) => r.app).filter((n) => n.toLowerCase()[0] === q[0]).slice(0, 8) };
      }
      return hit;
    }

    case 'build_queue': {
      const a = args || {};
      let q = d.build_queue || [];
      if (a.lane) q = q.filter((r) => r.lane === a.lane);
      return {
        note: 'Apps absent from the Composio catalog, ranked by build score. Weights in scoring_model.',
        scoring_model: d.scoring_model,
        total: q.length,
        queue: q.slice(0, a.limit ?? 15),
      };
    }

    case 'patterns':
      return { headline: d.headline, distributions: d.distributions, by_category: d.matrix };

    case 'verification': {
      const e = d.eval || {};
      const includeMisses = (args || {}).include_misses !== false;
      return {
        closed_book_baseline: e.pass1 && { accuracy: e.pass1.accuracy, per_field: e.pass1.per_field },
        grounded_and_critiqued: e.pass2 && { accuracy: e.pass2.accuracy, per_field: e.pass2.per_field },
        lift: e.lift,
        calibration: e.calibration_pass2,
        composio_oracle: e.oracle && {
          n_checked: e.oracle.n_checked,
          agreement: e.oracle.agreement,
          disagreements: e.oracle.disagreements,
        },
        misses: includeMisses && e.pass2 ? e.pass2.misses : undefined,
        caveat: 'Gold set is 20 apps / 100 labels. One flipped label moves accuracy by 1 point.',
      };
    }

    default:
      throw new Error(`unknown tool: ${name}`);
  }
}

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Headers', 'content-type, mcp-protocol-version, mcp-session-id, authorization');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');

  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method === 'GET') {
    return res.status(200).json({
      server: SERVER,
      transport: 'streamable-http',
      hint: 'POST JSON-RPC here. claude mcp add --transport http toolkit-radar <this-url>',
      tools: TOOLS.map((t) => t.name),
    });
  }
  if (req.method !== 'POST') return res.status(405).json({ error: 'method not allowed' });

  let body = req.body;
  if (typeof body === 'string') { try { body = JSON.parse(body); } catch { body = null; } }
  if (!body) return res.status(400).json({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'parse error' } });

  const messages = Array.isArray(body) ? body : [body];
  const replies = [];

  for (const msg of messages) {
    const { id, method, params } = msg || {};
    // Notifications carry no id and must not be answered.
    if (id === undefined || id === null) continue;

    try {
      let result;
      switch (method) {
        case 'initialize':
          result = {
            protocolVersion: params?.protocolVersion || PROTOCOL_VERSION,
            capabilities: { tools: { listChanged: false } },
            serverInfo: SERVER,
            instructions:
              'Findings on whether 100 SaaS apps can become agent toolkits today. ' +
              'Start with patterns() for the headline, build_queue() for the roadmap, ' +
              'verification() before trusting any of it.',
          };
          break;
        case 'ping':
          result = {};
          break;
        case 'tools/list':
          result = { tools: TOOLS };
          break;
        case 'tools/call': {
          const data = await call(params?.name, params?.arguments, req);
          result = { content: [{ type: 'text', text: JSON.stringify(data, null, 2) }] };
          break;
        }
        case 'resources/list':
          result = { resources: [] };
          break;
        case 'prompts/list':
          result = { prompts: [] };
          break;
        default:
          replies.push({ jsonrpc: '2.0', id, error: { code: -32601, message: `method not found: ${method}` } });
          continue;
      }
      replies.push({ jsonrpc: '2.0', id, result });
    } catch (err) {
      replies.push({ jsonrpc: '2.0', id, error: { code: -32603, message: String(err.message || err) } });
    }
  }

  if (!replies.length) return res.status(202).end();
  return res.status(200).json(Array.isArray(body) ? replies : replies[0]);
}
