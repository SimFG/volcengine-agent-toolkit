from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'plugins/volcengine-context-search/skills/volcengine-context-search'
SCRIPT = SKILL / 'scripts/ctxsearch.mjs'
NODE = shutil.which('node')


@unittest.skipUnless(NODE, 'Node.js is required for Context Search transport tests')
class ContextSearchTest(unittest.TestCase):
    def js(self, source):
        program = f'import * as cs from {json.dumps(SCRIPT.as_uri())};\n' + source
        result = subprocess.run([NODE, '--input-type=module', '-e', program],
                                text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def test_production_endpoint_and_no_routing_flags(self):
        result = self.js('''
const rejected = ['--env', '--service', '--host', '--x-tt-env', '--header'].map(flag => {
  try { cs.parseArguments([flag, 'dev']); return false; } catch { return true; }
});
console.log(JSON.stringify({endpoint: cs.ENDPOINT, rejected, help: cs.HELP}));
''')
        self.assertTrue(all(result['rejected']))
        self.assertEqual(result['endpoint']['service'], 'ctxsearch')
        for flag in ('--env', '--service', '--host', '--x-tt-env'):
            self.assertNotIn(flag, result['help'])

    def test_credentials_are_paired_and_session_token_supported(self):
        result = self.js('''
const chosen = cs.credentialsFrom({VOLC_ACCESSKEY:' example-ak ', VOLC_SECRETKEY:' example-sk ', VOLC_SESSION_TOKEN:'temporary'});
let rejected = false;
try { cs.credentialsFrom({CTXSEARCH_ACCESS_KEY_ID:'partial', VOLC_ACCESSKEY:'fallback', VOLC_SECRETKEY:'fallback'}); } catch { rejected = true; }
console.log(JSON.stringify({chosen, rejected}));
''')
        self.assertTrue(result['rejected'])
        self.assertEqual(result['chosen']['sessionToken'], 'temporary')
        self.assertEqual(result['chosen']['accessKeyId'], 'example-ak')

    def test_volcengine_credentials_have_highest_priority(self):
        result = self.js('''
console.log(JSON.stringify(cs.credentialsFrom({
 VOLCENGINE_ACCESS_KEY:' example-new-ak ', VOLCENGINE_SECRET_KEY:' example-new-sk ',
 VOLCENGINE_SESSION_TOKEN:' example-new-token ',
 CTXSEARCH_ACCESS_KEY_ID:'example-old-ak', CTXSEARCH_SECRET_ACCESS_KEY:'example-old-sk',
 CTXSEARCH_TEST_AK:'example-test-ak', CTXSEARCH_TEST_SK:'example-test-sk',
 VOLC_ACCESSKEY:'example-volc-ak', VOLC_SECRETKEY:'example-volc-sk',
 CTXSEARCH_SESSION_TOKEN:'example-old-token', VOLC_SESSION_TOKEN:'example-volc-token'
})));
''')
        self.assertEqual(result, {
            'accessKeyId': 'example-new-ak', 'secretAccessKey': 'example-new-sk',
            'sessionToken': 'example-new-token',
        })

    def test_empty_volcengine_pair_preserves_legacy_pair_priority(self):
        result = self.js('''
const pairs = [
 ['CTXSEARCH_ACCESS_KEY_ID', 'CTXSEARCH_SECRET_ACCESS_KEY'],
 ['CTXSEARCH_TEST_AK', 'CTXSEARCH_TEST_SK'],
 ['VOLC_ACCESSKEY', 'VOLC_SECRETKEY']
];
const results = pairs.map((_, start) => {
 const variables = {VOLCENGINE_ACCESS_KEY:' ', VOLCENGINE_SECRET_KEY:'', VOLCENGINE_SESSION_TOKEN:' '};
 for (let i = start; i < pairs.length; i++) {
  variables[pairs[i][0]] = `example-ak-${i}`;
  variables[pairs[i][1]] = `example-sk-${i}`;
 }
 return cs.credentialsFrom(variables);
});
console.log(JSON.stringify(results));
''')
        for index, row in enumerate(result):
            self.assertEqual(row['accessKeyId'], f'example-ak-{index}')
            self.assertEqual(row['secretAccessKey'], f'example-sk-{index}')
            self.assertNotIn('sessionToken', row)

    def test_partial_volcengine_pair_is_rejected_before_fallback(self):
        result = self.js('''
console.log(JSON.stringify([
 {VOLCENGINE_ACCESS_KEY:'example-partial-ak'},
 {VOLCENGINE_SECRET_KEY:'example-partial-sk'}
].map(partial => {
 try { cs.credentialsFrom({...partial, CTXSEARCH_ACCESS_KEY_ID:'example-old-ak', CTXSEARCH_SECRET_ACCESS_KEY:'example-old-sk'}); return false; }
 catch (error) { return error.message.includes('VOLCENGINE_ACCESS_KEY') && error.message.includes('VOLCENGINE_SECRET_KEY'); }
})));
''')
        self.assertTrue(all(result))

    def test_session_token_priority_and_whitespace_fallback(self):
        result = self.js('''
const credentials = {VOLCENGINE_ACCESS_KEY:'example-new-ak',VOLCENGINE_SECRET_KEY:'example-new-sk'};
const cases = [
 {VOLCENGINE_SESSION_TOKEN:' new-token ',CTXSEARCH_SESSION_TOKEN:'ctx-token',VOLC_SESSION_TOKEN:'volc-token'},
 {VOLCENGINE_SESSION_TOKEN:' ',CTXSEARCH_SESSION_TOKEN:' ctx-token ',VOLC_SESSION_TOKEN:'volc-token'},
 {VOLCENGINE_SESSION_TOKEN:' ',CTXSEARCH_SESSION_TOKEN:' ',VOLC_SESSION_TOKEN:' volc-token '},
 {VOLCENGINE_SESSION_TOKEN:' ',CTXSEARCH_SESSION_TOKEN:' ',VOLC_SESSION_TOKEN:' '}
];
console.log(JSON.stringify(cases.map(tokens => cs.credentialsFrom({...credentials,...tokens}).sessionToken ?? null)));
''')
        self.assertEqual(result, ['new-token', 'ctx-token', 'volc-token', None])

    def test_new_token_is_used_in_security_token_header(self):
        result = self.js('''
const credentials = cs.credentialsFrom({VOLCENGINE_ACCESS_KEY:'example-new-ak',VOLCENGINE_SECRET_KEY:'example-new-sk',VOLCENGINE_SESSION_TOKEN:'example-new-token'});
const signed = cs.buildSignedRequest({action:'ListScene', credentials, date:new Date('2026-01-02T03:04:05Z')});
console.log(JSON.stringify({token:signed.headers['X-Security-Token'], authorization:signed.headers.Authorization}));
''')
        self.assertEqual(result['token'], 'example-new-token')
        self.assertIn('Credential=example-new-ak/', result['authorization'])
        self.assertIn('SignedHeaders=x-date;x-security-token', result['authorization'])

    def test_new_credentials_are_removed_from_local_error_messages(self):
        canary = 'SYNTHETIC_ERROR_CANARY_NOT_A_REAL_SECRET'
        for name in ('VOLCENGINE_ACCESS_KEY', 'VOLCENGINE_SECRET_KEY', 'VOLCENGINE_SESSION_TOKEN'):
            with self.subTest(name=name):
                clean = {k: v for k, v in os.environ.items()
                         if not k.startswith(('VOLCENGINE_', 'CTXSEARCH_', 'VOLC_'))}
                clean[name] = canary
                result = subprocess.run([NODE, str(SCRIPT), '--action', 'ListScene',
                                         '--unknown-' + canary], env=clean,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn(canary, result.stderr)
                self.assertIn('[REDACTED]', result.stderr)

    def test_v4_post_signature_matches_independent_implementation(self):
        result = self.js('''
console.log(JSON.stringify(cs.buildSignedRequest({
 action:'ExampleAction',method:'POST',query:{Project:'default',Tag:['a b','a+b','汉字']},
 body:{Name:'测试'},credentials:{accessKeyId:'example-ak',secretAccessKey:'example-sk',sessionToken:'temporary'},
 date:new Date('2026-01-02T03:04:05Z')
})));
''')
        body = '{"Name":"测试"}'
        esc = lambda value: quote(value, safe='-_.~')
        values = [('Action', 'ExampleAction'), ('Version', '2025-09-01'),
                  ('Project', 'default'), *[('Tag', tag) for tag in ['a b', 'a+b', '汉字']]]
        query = '&'.join(f'{k}={v}' for k, v in sorted((esc(k), esc(v)) for k, v in values))
        sha = lambda value: hashlib.sha256(value.encode()).hexdigest()
        headers = 'x-content-sha256:' + sha(body) + '\nx-date:20260102T030405Z\nx-security-token:temporary\n'
        names = 'x-content-sha256;x-date;x-security-token'
        canonical = '\n'.join(['POST', '/', query, headers, names, sha(body)])
        scope = '20260102/cn-beijing/ctxsearch/request'
        to_sign = '\n'.join(['HMAC-SHA256', '20260102T030405Z', scope, sha(canonical)])
        key = b'example-sk'
        for step in ('20260102', 'cn-beijing', 'ctxsearch', 'request'):
            key = hmac.new(key, step.encode(), hashlib.sha256).digest()
        signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        expected = f'HMAC-SHA256 Credential=example-ak/{scope}, SignedHeaders={names}, Signature={signature}'
        self.assertEqual(result['headers']['Authorization'], expected)
        self.assertEqual(result['payload'], body)
        self.assertEqual(int(result['headers']['Content-Length']), len(body.encode()))
        self.assertNotIn('x-tt-env', result['headers'])

    def test_get_signature_without_body_and_token(self):
        result = self.js('''
console.log(JSON.stringify(cs.buildSignedRequest({action:'ListScene',query:{PageNumber:1},
credentials:{accessKeyId:'example-ak',secretAccessKey:'example-sk'},date:new Date('2026-01-02T03:04:05Z')})));
''')
        self.assertEqual(result['method'], 'GET')
        self.assertNotIn('payload', result)
        self.assertNotIn('X-Security-Token', result['headers'])
        self.assertIn('SignedHeaders=x-date', result['headers']['Authorization'])

    def test_query_and_body_validation(self):
        result = self.js('''
const bad = [
 () => cs.queryEntries({Action:'DeleteScene'}),
 () => cs.queryEntries({Version:'other'}),
 () => cs.queryEntries({Nested:{key:'value'}}),
 () => cs.queryEntries({Items:[null]}),
 () => cs.parseArguments(['--action','ListScene','--action','GetScene']),
 () => cs.parseArguments(['--action','--method','GET']),
 () => cs.buildSignedRequest({action:'ListScene',method:'GET',body:{},credentials:{}}),
 () => cs.callInput({'--query':'{}','--query-file':'unused'}, {VOLC_ACCESSKEY:'a',VOLC_SECRETKEY:'b'}),
 () => cs.callInput({'--query-stdin':true,'--body-stdin':true}, {}, () => '{}')
].map(fn => {try {fn(); return false;} catch {return true;}});
console.log(JSON.stringify(bad));
''')
        self.assertTrue(all(result))

    def test_response_exit_codes_and_json_redaction(self):
        result = self.js('''
const sensitive = {ResponseMetadata:{},Result:{Key:'secret',UploadUrl:'https://secret',
 ObjectKey:'private/object',ApiKey:'private-key',Password:'password',Variables:[{Secret:true,Value:'secret-value'}]}};
console.log(JSON.stringify([
 cs.classifyResponse({status:200,text:JSON.stringify(sensitive)}),
 cs.classifyResponse({status:200,text:JSON.stringify({ResponseMetadata:{Error:{Code:'Denied'}}})}),
 cs.classifyResponse({status:403,text:'denied'}),
 cs.classifyResponse({status:200,text:'data: test\\n\\n'})
]));
''')
        self.assertEqual([item['code'] for item in result], [0, 1, 1, 0])
        redacted = json.loads(result[0]['text'])['Result']
        self.assertEqual(redacted['ApiKey'], '[REDACTED]')
        self.assertEqual(redacted['Variables'][0]['Value'], '[REDACTED]')
        self.assertEqual(redacted['UploadUrl'], '[REDACTED]')

    def test_missing_credentials_does_not_make_network_request(self):
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith(('VOLCENGINE_', 'CTXSEARCH_', 'VOLC_'))}
        result = subprocess.run([NODE, str(SCRIPT), '--action', 'ListScene'],
                                env=clean, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertIn('缺少凭据', result.stderr)


class ContextSearchPackageTest(unittest.TestCase):
    def test_catalog_contains_optional_plugin(self):
        catalog = json.loads((ROOT / 'skills/core/volcengine-find-skills/references/catalog.json').read_text())
        entry = next(p for p in catalog['plugins'] if p['name'] == 'volcengine-context-search')
        self.assertFalse(entry['default'])
        self.assertEqual(entry['skills'][0]['path'], SKILL.relative_to(ROOT).as_posix())

    def test_no_internal_service_or_source_repository_dependency(self):
        forbidden = ('ctxsearch_dev', 'ctxsearch_stg', 'ark-context-search-cli', '--env', '--x-tt-env')
        for path in SKILL.rglob('*'):
            if path.is_file():
                content = path.read_text(encoding='utf-8')
                for item in forbidden:
                    self.assertNotIn(item, content, str(path))


if __name__ == '__main__':
    unittest.main()
