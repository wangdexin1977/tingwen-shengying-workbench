"""本地会话恢复与安全边界；不调用模型、不修改用户任务。"""
import re
import subprocess
import unittest
from unittest.mock import patch

import app as server


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_expired_request_is_rejected_before_business_handler(self):
        with patch.object(server, "launch") as launch:
            response = self.client.post("/api/jobs", json={}, headers={"X-Local-Token": "old-token"})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json["code"], "local_session_expired")
            self.assertIn("会话已过期", response.json["error"])
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            launch.assert_not_called()

    def test_missing_token_does_not_request_automatic_replay(self):
        response = self.client.post("/api/jobs", json={})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json["code"], "local_session_missing")

    def test_session_and_html_are_not_cached(self):
        for path in ["/", "/api/session"]:
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        self.assertEqual(self.client.get("/api/session").json,
                         {"product": "tingwen-shengying-workbench", "token": server.TOKEN})

    def test_cross_origin_and_foreign_hosts_remain_blocked(self):
        for path, method in [("/api/session", "GET"), ("/api/jobs", "POST")]:
            for headers in [{"Origin": "https://example.com"}, {"Origin": "null"},
                            {"Origin": "http://localhost:8092"}, {"Sec-Fetch-Site": "cross-site"}]:
                response = self.client.open(path, method=method, json={} if method == "POST" else None,
                                            headers={"X-Local-Token": server.TOKEN, **headers})
                self.assertEqual(response.status_code, 403)
                self.assertNotIn(server.TOKEN, response.get_data(as_text=True))
                self.assertIn("不是来自当前工作台", response.json["error"])
            response = self.client.open(path, method=method, base_url="http://evil.example",
                                        headers={"X-Local-Token": server.TOKEN})
            self.assertEqual(response.status_code, 403)

    def test_rotated_token_recovers_without_duplicate_side_effect(self):
        old = server.TOKEN
        with patch.object(server, "TOKEN", "b" * 43), patch.object(server.subprocess, "Popen") as opened:
            origin = "http://127.0.0.1:8091"
            response = self.client.post("/api/videos/reveal-root", json={}, base_url=origin,
                                        headers={"X-Local-Token": old, "Origin": origin})
            self.assertEqual(response.status_code, 403)
            opened.assert_not_called()
            fresh = self.client.get("/api/session", base_url=origin, headers={"Origin": origin}).json["token"]
            response = self.client.post("/api/videos/reveal-root", json={}, base_url=origin,
                                        headers={"X-Local-Token": fresh, "Origin": origin})
            self.assertEqual(response.status_code, 200)
            opened.assert_called_once()

    def test_browser_recovery_retry_limits_and_no_unknown_result_replay(self):
        page = self.client.get("/").get_data(as_text=True)
        script = re.search(r"let sessionRefresh=null;(.*?)function auth\(", page, re.S).group(1)
        harness = r"""
const assert=require('node:assert/strict');
let token='a'.repeat(43),sessionRefresh=null;
const form={text:'文案'.repeat(1200),title:'标题',photoUrl:'https://example.com/photo.jpg',file:{name:'照片.png'}};
const before=JSON.stringify(form),payload={...form,payment_consent:true,api_key:'test-only'};
function showError(){}
const response=(status,data)=>({status,ok:status>=200&&status<300,json:async()=>data});
const expired=()=>response(403,{code:'local_session_expired',error:'过期'});
const session=()=>response(200,{product:'tingwen-shengying-workbench',token:'b'.repeat(43)});
let calls=[];
function queue(items){calls=[];token='a'.repeat(43);global.fetch=async(path,options)=>{calls.push({path,options});const item=items.shift();if(item instanceof Error)throw item;if(!item)throw Error('unexpected replay');return item;};}
async function main(){
  queue([expired(),session(),response(200,{id:'ok'})]);
  assert.equal((await api('/api/jobs',payload)).id,'ok');
  assert.equal(calls.length,3);assert.equal(calls[1].path,'/api/session');
  assert.equal(calls[1].options.cache,'no-store');
  assert.equal(calls[0].options.body,calls[2].options.body);
  assert.equal(calls[0].options.headers['X-Local-Token'],'a'.repeat(43));
  assert.equal(calls[2].options.headers['X-Local-Token'],'b'.repeat(43));
  assert.equal(JSON.stringify(form),before);
  queue([expired(),session(),expired()]);await assert.rejects(api('/api/jobs',payload));assert.equal(calls.length,3);
  for(const item of [response(403,{error:'provider denied'}),response(403,{code:'local_session_missing'}),response(500,{error:'结果未知'}),new Error('timeout'),{status:200,ok:true,json:async()=>{throw Error('invalid json')}}]){
    queue([item]);await assert.rejects(api('/api/jobs',payload));assert.equal(calls.length,1);
  }
  for(const item of [response(200,{product:'wrong',token:'b'.repeat(43)}),response(200,{product:'tingwen-shengying-workbench',token:'bad'}),response(403,{error:'denied'}),new Error('offline')]){
    queue([expired(),item]);await assert.rejects(api('/api/jobs',payload));assert.equal(calls.length,2);
  }
  queue([expired()]);await assert.rejects(api('/api/jobs'));assert.equal(calls.length,1);
  let resolveSession,refreshCount=0;
  global.fetch=(path)=>{assert.equal(path,'/api/session');refreshCount++;return new Promise(resolve=>{resolveSession=resolve})};
  const first=renewSession(),second=renewSession();assert.equal(refreshCount,1);
  resolveSession(session());await Promise.all([first,second]);assert.equal(sessionRefresh,null);
  assert.equal(JSON.stringify(form),before);
}
"""
        result = subprocess.run(["/opt/homebrew/bin/node", "-"], input=harness + script + "main().catch(e=>{console.error(e);process.exitCode=1});",
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
