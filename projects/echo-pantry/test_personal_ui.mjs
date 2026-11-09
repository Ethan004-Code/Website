import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import * as limits from "./limits.js";
import * as allergens from "./allergens.js";

function ui(fetchImpl = async () => { throw Error("Unexpected network request"); }) {
  const nodes = new Map();
  const listeners = new Map();
  const prompts = [];
  let consent = true;
  const document = {
    body: {style: {}},
    querySelector(sel) {
      if (sel === "#review-panel") return null;
      if (!nodes.has(sel)) nodes.set(sel, {
        innerHTML: "", hidden: true, style: {}, addEventListener() {},
        classList: { toggle() {} }, contains() { return false; },
      });
      return nodes.get(sel);
    },
    getElementById(id) { return nodes.get(`#${id}`) || null; },
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
  };
  const context = vm.createContext({
    ...limits, ...allergens, document, fetch: fetchImpl, Date, Intl, URL,
    window: {scrollTo() {}, confirm(message) { prompts.push(message); return consent; }}, history: {replaceState() {}},
    createDonationMap() {}, setInterval() {}, setTimeout() {}, clearTimeout() {},
  });
  const source = fs.readFileSync(new URL("./new.js", import.meta.url), "utf8")
    .replace(/^import .*;$/gm, "").split("\ninitialize();")[0];
  vm.runInContext(source + `
    globalThis.ui = { S, viewImpact, refreshImpact, conditionOptions,
      viewCompose, viewReview, onBind, readDraft, signedOut,
      spotField, viewDiscover, visibleListings, giveawayBody, giveawayBlock, viewActivity, listingCard, actions,
      wantedText, wantedItemsOf, wantedItemText, offerBlock, requestCard, openRequestForm, fromApiItem,
      wantedBody, crCard, loadCommunity, respondBody, sendRespond, refreshActivity,
      openListing, ratingSummary, fallbackBody, S,
      openSwapChoices, proposeSwap, swapChoiceCard, swapQuantityFields, selectedSwapItems };
  `, context);
  return { ...context.ui, nodes, prompts, setConsent(value) { consent = value; }, dispatch(type, target) {
    for (const handler of listeners.get(type) || []) handler({type, target});
  }, submit(form) {
    return Promise.all((listeners.get("submit") || []).map(handler =>
      handler({target: form, preventDefault() {}})));
  } };
}

test("signed out impact has sign-in button, no totals, and makes no impact request", async () => {
  const app = ui();
  const html = app.viewImpact();
  assert.match(html, /Your impact/);
  assert.match(html, /Sign in to see your impact/);
  assert.match(html, /data-action="open-signin"/);
  assert.doesNotMatch(html, /class="stat/);
  await app.refreshImpact();
  assert.equal(app.S.impact, null);
});

const wanted = {
  id: "wanted", user_id: "b", name: "Bea", items: ["onions"], quantity: null, unit: "each",
  location: "Library steps", pickup_start: "2026-12-01T18:00:00Z",
  pickup_end: "2026-12-01T19:00:00Z", deadline: "2026-12-01T20:00:00Z", status: "open",
};

test("listing cards and details show owner rating/count or the unrated fallback", () => {
  const app = ui();
  const base = {
    id: "rating-listing", user_id: "owner", name: "Etan", location: "Library steps",
    mode: "giveaway", items: ["Apples"], item_details: [{name:"Apples",quantity:3,unit:"each",category:"produce"}],
    pickup_start: "2026-12-01T18:00:00Z", pickup_end: "2026-12-01T19:00:00Z",
    deadline: "2026-12-01T20:00:00Z", status: "open", allergens: "None",
  };
  const rated = {...base, id:"rated", rating:4.8, rating_count:12};
  const unrated = {...base, id:"unrated", rating:null, rating_count:0};
  assert.match(app.listingCard(rated), /★ 4\.8 \(12 reviews\)/);
  assert.match(app.listingCard(unrated), /No ratings yet/);
  app.S.listings = [rated, unrated];
  app.openListing("rated");
  assert.match(app.nodes.get("#sheet").innerHTML, /Etan · ★ 4\.8 \(12 reviews\)/);
  app.openListing("unrated");
  assert.match(app.nodes.get("#sheet").innerHTML, /Etan · No ratings yet/);
  assert.equal(app.ratingSummary({...base, rating:5, rating_count:1}), "★ 5.0 (1 review)");
  assert.equal(app.ratingSummary({...base, rating:NaN, rating_count:1}), "No ratings yet");
});

test("Looking for has signed-out, loading, error, empty and real request states", () => {
  const app = ui();
  app.S.board = "wanted";
  assert.match(app.viewDiscover(), /Looking for/);
  assert.match(app.wantedBody(), /Sign in to see what neighbors are looking for/);
  app.S.user = {id: "a"};
  assert.match(app.wantedBody(), /class="skel"/);
  app.S.cr.error = "Temporarily unavailable";
  assert.match(app.wantedBody(), /Couldn't load requests/);
  assert.match(app.wantedBody(), /reload-wanted/);
  app.S.cr.error = "";
  app.S.cr.loaded = true;
  assert.match(app.wantedBody(), /No one is looking for anything yet/);
  app.S.cr.list = [wanted];
  assert.match(app.wantedBody(), /Any amount/);
  assert.match(app.wantedBody(), /Bea/);
  assert.match(app.wantedBody(), /Library steps/);
  assert.match(app.wantedBody(), /open-respond/);
  app.S.user.id = "b";
  assert.match(app.wantedBody(), /View in Getting/);
  assert.doesNotMatch(app.wantedBody(), /open-respond/);
});

test("Food browsing retains its filters and excludes donation UI; requests escape user text", () => {
  const app = ui();
  assert.match(app.viewDiscover(), /filter-mode/);
  assert.doesNotMatch(app.viewDiscover(), /open-donation-locations|Donation locations/);
  const html = app.crCard({...wanted, items:["<script>"], name:"<img>"});
  assert.doesNotMatch(html, /<script>|<img>/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(app.crCard({...wanted, quantity: 3}), /3 onions/);
});

test("food categories filter bundles and combine with search, type, pickup and deadline", () => {
  const app = ui();
  const due = new Date(Date.now() + 5 * 60 * 60 * 1000).toISOString();
  const item = (name, food_category) => ({name, quantity:1, unit:"each", category:"produce", food_category});
  const make = (id, items, mode = "giveaway", location = "Library steps") => ({
    id, mode, location, item_details:items, status:"open", deadline:due,
  });
  app.S.loaded = true;
  app.S.listings = [
    make("apple", [item("Apples", "fruits")]),
    make("watermelon", [item("Watermelon", "fruits")]),
    make("onions", [item("Onions", "vegetables")], "exchange", "Community Center"),
    make("snacks", [item("Crackers", "snacks")]),
    make("bundle", [item("Tomatoes", "vegetables"), item("Rice", "grains_pantry")]),
    make("legacy", [{name:"Oats", quantity:1, category:"shelf_stable"}]),
  ];
  app.S.filter.foodCategory = "fruits";
  assert.deepEqual(Array.from(app.visibleListings(), row => row.id), ["apple", "watermelon"]);
  app.S.filter.foodCategory = "vegetables";
  app.S.filter.foodSearch = "onion";
  app.S.filter.mode = "exchange";
  app.S.filter.pickup = "community";
  app.S.filter.hours = 24;
  assert.deepEqual(Array.from(app.visibleListings(), row => row.id), ["onions"]);
  app.S.filter = {mode:"", hours:0, foodCategory:"", foodSearch:"", pickup:""};
  assert.equal(app.visibleListings().length, 6);
  app.S.filter.foodCategory = "snacks";
  assert.deepEqual(Array.from(app.visibleListings(), row => row.id), ["snacks"]);
  app.S.filter.foodCategory = "";
  const bundleCard = app.listingCard(app.S.listings.find(row => row.id === "bundle"));
  assert.match(bundleCard, /Tomatoes.*Vegetables/);
  assert.match(bundleCard, /Rice.*Grains and pantry staples/);
  app.S.filter.foodCategory = "other";
  assert.deepEqual(Array.from(app.visibleListings(), row => row.id), ["legacy"]);
  assert.match(app.listingCard(app.S.listings.find(row => row.id === "legacy")), /Other/);
  app.S.filter.foodSearch = "not listed";
  assert.equal(app.visibleListings().length, 0);
  assert.match(app.viewDiscover(), /Nothing matches these filters/);
});

test("Looking for refresh reflects new data and discards previous-account responses", async () => {
  let finish;
  const app = ui(() => new Promise(resolve => {finish = resolve;}));
  app.S.user = {id:"a"}; app.S.board = "wanted";
  const first = app.loadCommunity();
  finish({ok:true,json:async()=>({requests:[wanted]})});
  await first;
  assert.equal(app.S.cr.list[0].id, "wanted");
  const late = app.loadCommunity();
  app.S.user = {id:"b"};
  finish({ok:true,json:async()=>({requests:[{...wanted,id:"stale"}]})});
  await late;
  assert.equal(app.S.cr.list[0].id, "wanted");
  const next = app.loadCommunity();
  assert.equal(app.S.cr.list.length, 0);
  finish({ok:true,json:async()=>({requests:[{...wanted,id:"fresh"}]})});
  await next;
  assert.equal(app.S.cr.list[0].id, "fresh");
});

test("Offer food sheet presents full amounts, explicit swap listing and non-reservation consent", () => {
  const app = ui();
  const listing = {id:"one",mode:"giveaway",item_details:[{name:"onions",quantity:3,unit:"each"},{name:"apples",quantity:2,unit:"each"}]};
  app.S.respond = {data:{request:wanted, options:[{listing,return_listings:[],location:wanted.location,pickup_start:wanted.pickup_start,pickup_end:wanted.pickup_end}]}};
  assert.match(app.respondBody(), /3 onions, 2 apples/i);
  assert.match(app.respondBody(), /Offer for free/);
  assert.match(app.respondBody(), /Nothing is reserved until Bea accepts/);
  const option = app.S.respond.data.options[0];
  option.listing.mode = "exchange";
  option.return_listings = [{id:"two",item_details:[{name:"cans of beans",quantity:4,unit:"each"}],location:wanted.location,offer_pickup_start:wanted.pickup_start,offer_pickup_end:wanted.pickup_end}];
  assert.match(app.respondBody(), /4 cans of beans/i);
  assert.match(app.respondBody(), /data-match="two"/);
  assert.match(app.respondBody(), /Proposed pickup/);
  app.S.respond.data.options = [];
  assert.match(app.respondBody(), /Swaps can use different listing locations/);
  assert.match(app.respondBody(), /Share food/);
});

test("Offer food failure stays inline, blocks double sends and supports stale option refresh", async () => {
  let calls = 0, finish;
  const app = ui(() => {calls++; return new Promise(resolve => {finish = resolve;});});
  app.S.user = {id:"a"};
  app.S.respond = {id:"wanted",uid:"a",tok:0,busy:false,data:{request:wanted,options:[]}};
  app.nodes.set("#sheet-root", {hidden:false});
  const first = app.sendRespond({dataset:{listing:"one"}});
  await app.sendRespond({dataset:{listing:"one"}});
  assert.equal(calls,1);
  finish({ok:false,status:409,json:async()=>({error:"Listing changed"})});
  await first;
  assert.match(app.S.respond.error, /Listing changed/);
  assert.equal(app.S.respond.stale,true);
  assert.equal(app.S.respond.busy,false);
  assert.equal(app.S.tab,"discover");
});

test("Owner-offered giveaway gives the receiver Accept and Decline, sender only waits", () => {
  const app = ui();
  const row = {id:"handoff",listing_id:"one",owner_id:"a",owner_name:"Alice",requester_id:"b",requester_name:"Bea",status:"pending",accepted_by:["a"],item_details:[{name:"onions",quantity:3,unit:"each"}]};
  app.S.user = {id:"b"};
  const receiver = app.giveawayBlock(row);
  assert.match(receiver,/Alice<\/b> offered you/);
  assert.match(receiver,/data-accept="1"/);
  assert.match(receiver,/data-accept="0"/);
  assert.doesNotMatch(receiver,/>Approve</);
  app.S.user = {id:"a"};
  const sender = app.giveawayBlock(row);
  assert.match(sender,/Waiting for Bea to accept/);
  assert.match(sender,/Withdraw offer/);
  assert.doesNotMatch(sender,/data-accept="1"/);
  row.accepted_by = ["b"];
  assert.match(app.giveawayBlock(row),/>Approve</);
  row.status = "approved"; row.accepted_by = ["a","b"];
  assert.match(app.giveawayBlock(row),/Giveaway to/);
});

test("Late Activity response cannot restore another account's handoffs", async () => {
  const finish = [];
  const app = ui(() => new Promise(resolve => {finish.push(resolve);}));
  app.S.user = {id:"a"};
  const pending = app.refreshActivity(true);
  app.S.user = {id:"b"};
  finish.forEach(resolve => resolve({ok:true,json:async()=>({requests:[wanted], offers:[{id:"private"}],listings:[],notifications:[]})}));
  await pending;
  assert.equal(app.S.myOffers.length,0);
  assert.equal(app.S.myRequests.length,0);
  assert.equal(app.S.activityLoaded,false);
});

test("Offer a swap checks all own listings against the clicked target, not the first listing", async () => {
  const seen = [];
  const target = {id:"target",name:"Bea",location:"Community Center"};
  const match = {listing:target,give_items:[{name:"onions",quantity:3,unit:"each"}],
    receive_items:[{name:"rice",quantity:2,unit:"each"}],location:"Library steps",
    pickup_start:wanted.pickup_start,pickup_end:wanted.pickup_end};
  const app = ui(async path => {
    seen.push(path);
    const data = path.includes("?mine=true") ? {listings:[
      {id:"wrong",user_id:"a",mode:"exchange",status:"open"},
      {id:"right",user_id:"a",mode:"exchange",status:"open"}]}
      : path.includes("/right/matches") ? {matches:[match]}
      : {matches:[{...match,listing:{id:"another-target"}}]};
    return {ok:true,json:async()=>data};
  });
  app.S.user = {id:"a"}; app.S.listings = [target];
  await app.openSwapChoices("target");
  assert.equal(app.S.swapChoices.options.length,1);
  assert.equal(app.S.swapChoices.options[0].ownId,"right");
  assert.ok(seen.some(path => path.includes("/listings/wrong/matches")));
  assert.ok(seen.some(path => path.includes("/listings/right/matches")));
  const html = app.nodes.get("#sheet-body").innerHTML;
  assert.match(html,/3 onions/);
  assert.match(html,/2 rice/);
  assert.match(html,/Community Center/);
  assert.match(html,/value="Library steps"/);
  assert.match(html,/maxlength="100"/);
  assert.match(html,/aria-label="You offer: 3 onions, minimum 1"/);
  assert.match(html,/aria-label="You receive: 2 rice, minimum 1"/);
  assert.match(html,/Different listing locations are OK/);
});

test("Swap chooser reports reciprocal-food or pickup-time reason for the selected listing", async () => {
  const app = ui(async path => ({ok:true,json:async()=>path.includes("?mine=true")
    ? {listings:[{id:"right",user_id:"a",mode:"exchange",status:"open"}]}
    : {matches:[],incompatibilities:[{listing_id:"target",message:"Pickup windows do not overlap before the listings expire."}]}}));
  app.S.user = {id:"a"}; app.S.listings = [{id:"target",name:"Food",location:"Park"}];
  await app.openSwapChoices("target");
  const html = app.nodes.get("#sheet-body").innerHTML;
  assert.match(html,/No compatible swap with this listing/);
  assert.match(html,/Pickup windows do not overlap/);
  assert.doesNotMatch(html,/quantity mismatch|different pickup spots/i);
});

test("Selected swap posts the chosen listing pair and agreed spot, rejecting empty or long spots", async () => {
  const payloads = [];
  const app = ui(async (path, options={}) => {
    if (options.method === "POST") {
      payloads.push(JSON.parse(options.body));
      return {ok:false,status:400,json:async()=>({error:"Test rejection; no writes"})};
    }
    return {ok:true,json:async()=>({})};
  });
  app.S.user = {id:"a"};
  app.S.swapChoices = {options:[{ownId:"right",listing:{id:"target"},
    give_items:[{name:"onions",quantity:3,unit:"each"}],receive_items:[{name:"rice",quantity:2,unit:"each"}],
    pickup_start:wanted.pickup_start,pickup_end:wanted.pickup_end}]};
  app.nodes.set("#swapqty-choice-0-give-0",{value:"3"});
  app.nodes.set("#swapqty-choice-0-receive-0",{value:"2"});
  const button = {dataset:{idx:"0"},disabled:false};
  app.nodes.set("#swap-spot-0",{value:""});
  app.nodes.set("#swapqty-choice-0-give-0",{value:"2"});
  app.nodes.set("#swapqty-choice-0-receive-0",{value:"1"});
  app.nodes.get("#swapqty-choice-0-give-0").value = "0";
  await app.proposeSwap(button,true);
  app.nodes.get("#swapqty-choice-0-give-0").value = "4";
  await app.proposeSwap(button,true);
  app.nodes.get("#swapqty-choice-0-give-0").value = "";
  await app.proposeSwap(button,true);
  app.nodes.get("#swapqty-choice-0-give-0").value = "2";
  app.nodes.get("#swap-spot-0").value = "x".repeat(101);
  await app.proposeSwap(button,true);
  assert.equal(payloads.length,0);
  app.nodes.get("#swap-spot-0").value = "Park entrance";
  await app.proposeSwap(button,true);
  assert.deepEqual(payloads,[{listing_id:"right",match_id:"target",location:"Park entrance",give_quantities:[2],receive_quantities:[1]}]);
  assert.match(app.prompts[0],/You give: 2 onions/);
  assert.match(app.prompts[0],/You get: 1 rice/);
  assert.match(app.prompts[0],/Park entrance/);
  assert.match(app.prompts[0],/The amounts may differ/);
  assert.equal(button.disabled,false);
});

test("Request swap responses send proposed location and preserve it after a failed send", async () => {
  let payload;
  const app = ui(async (path, options) => {
    payload = JSON.parse(options.body);
    return {ok:false,status:409,json:async()=>({error:"Availability changed"})};
  });
  app.S.user = {id:"a"};
  app.S.respond = {id:"wanted",uid:"a",busy:false,data:{request:wanted,options:[{
    listing:{id:"right",item_details:[{name:"onions",quantity:3,unit:"each"}]},
    return_listings:[{id:"target",item_details:[{name:"rice",quantity:2,unit:"each"}],
      offer_pickup_start:wanted.pickup_start,offer_pickup_end:wanted.pickup_end}]
  }]}};
  app.nodes.set("#respond-spot-0",{value:"Public park entrance"});
  app.nodes.set("#swapqty-respond-0-0-give-0",{value:"2"});
  app.nodes.set("#swapqty-respond-0-0-receive-0",{value:"1"});
  await app.sendRespond({dataset:{listing:"right",match:"target",spot:"respond-spot-0",
    giveQuantities:"respond-0-0-give",receiveQuantities:"respond-0-0-receive"}});
  assert.deepEqual(payload,{listing_id:"right",match_id:"target",location:"Public park entrance",give_quantities:[2],receive_quantities:[1]});
  assert.match(app.prompts[0],/You give: 2 onions/);
  assert.match(app.prompts[0],/You get: 1 rice/);
  assert.equal(app.S.respond.locations.right,"Public park entrance");
  assert.equal(app.S.respond.stale,true);
});

test("Unequal swap amounts need explicit consent at proposal; cancelling does not POST", async () => {
  let posts = 0;
  const app = ui(async () => { posts++; throw Error("Should not submit without consent"); });
  app.S.user = {id:"a"};
  app.S.swapChoices = {options:[{ownId:"right",listing:{id:"target"},
    give_items:[{name:"onions",quantity:3,unit:"each"}],receive_items:[{name:"rice",quantity:2,unit:"each"}],
    pickup_start:wanted.pickup_start,pickup_end:wanted.pickup_end}]};
  app.nodes.set("#swapqty-choice-0-give-0",{value:"3"});
  app.nodes.set("#swapqty-choice-0-receive-0",{value:"2"});
  app.nodes.set("#swap-spot-0",{value:"Park entrance"});
  app.setConsent(false);
  const button = {dataset:{idx:"0"},disabled:false};
  await app.proposeSwap(button,true);
  assert.equal(posts,0);
  assert.equal(button.disabled,false);
  assert.match(app.prompts[0],/You give: 3 onions/);
  assert.match(app.prompts[0],/You get: 2 rice/);
});

test("Receiver sees their own full amounts before accepting; cancelling prevents reservation", async () => {
  let posts = 0;
  const app = ui(async (path, options) => {
    if (options?.method !== "POST") return {ok:true,json:async()=>({})};
    posts++;
    assert.equal(path,"/api/offers/swap/respond");
    assert.deepEqual(JSON.parse(options.body),{accept:true});
    return {ok:false,status:409,json:async()=>({error:"Swap changed"})};
  });
  app.S.user = {id:"b"};
  app.S.myOffers = [{id:"swap",listing_id:"right",match_id:"target",participants:[{user_id:"a"},{user_id:"b"}],
    give_items:[{name:"onions",quantity:3,unit:"each"}],receive_items:[{name:"rice",quantity:2,unit:"each"}],
    location:"Park entrance",pickup_start:wanted.pickup_start,pickup_end:wanted.pickup_end}];
  app.setConsent(false);
  const button = {dataset:{id:"swap",accept:"1"},disabled:false};
  await app.actions["offer-respond"](button);
  assert.equal(posts,0);
  assert.equal(button.disabled,false);
  assert.match(app.prompts[0],/You give: 2 rice/);
  assert.match(app.prompts[0],/You get: 3 onions/);
  app.setConsent(true);
  await app.actions["offer-respond"](button);
  assert.equal(posts,1);
  assert.equal(button.disabled,false);
});

test("signed in all-zero impact has the exact requested message", () => {
  const app = ui();
  app.S.user = { id: "a", display_name: "A" };
  app.S.impact = { completed_trades: 0, completed_giveaways: 0, donations_reported: 0, compost_reported: 0 };
  assert.match(app.viewImpact(), /No completed handoffs yet\. Share or pick up food to get started\./);
});

test("nonzero personal impact renders own counts without the empty message", () => {
  const app = ui();
  app.S.user = { id: "a", display_name: "A" };
  app.S.impact = { completed_trades: 2, completed_giveaways: 1, donations_reported: 0, compost_reported: 0 };
  const html = app.viewImpact();
  assert.match(html, /<b>2<\/b>/);
  assert.doesNotMatch(html, /Donations|Compost|Reported by you/);
  assert.doesNotMatch(html, /No completed handoffs/);
});

test("late response for a previous account cannot populate current impact", async () => {
  let finish;
  const app = ui(() => new Promise(resolve => { finish = resolve; }));
  app.S.user = { id: "a" };
  const pending = app.refreshImpact();
  app.S.user = { id: "b" };
  finish({ ok: true, json: async () => ({ completed_trades: 999 }) });
  await pending;
  assert.equal(app.S.impact, null);
});

test("newer impact refresh wins even if an earlier request finishes last", async () => {
  const requests = [];
  const app = ui(() => new Promise(resolve => requests.push(resolve)));
  app.S.user = { id: "a" };
  const old = app.refreshImpact(), current = app.refreshImpact();
  requests[1]({ ok: true, json: async () => ({ completed_trades: 2 }) });
  await current;
  requests[0]({ ok: true, json: async () => ({ completed_trades: 1 }) });
  await old;
  assert.equal(app.S.impact.completed_trades, 2);
});

test("Share explains Bob and never offers Opened package", () => {
  const app = ui();
  const message = "Bob is RePlate's AI helper. It drafts your listing and checks it before it goes live.";
  assert.ok(app.viewCompose().includes(message));
  assert.ok(app.viewReview().includes(message));
  assert.doesNotMatch(app.conditionOptions(""), /Opened package|value="opened"/);
});

test("share requires an editable food category per item and old listings default to Other", () => {
  const app = ui();
  app.S.draft.items = [{name:"", quantity:"", condition:"", food_category:"", printed_date:"", date_type:""}];
  assert.match(app.viewReview(), /Food category/);
  assert.match(app.viewReview(), /value="fruits"/);
  assert.match(app.readDraft().miss.join(" "), /choose a food category/);
  app.S.draft.items[0].food_category = "fruits";
  assert.doesNotMatch(app.readDraft().miss.join(" "), /choose a food category/);
  assert.equal(app.fromApiItem({name:"Apples"}).food_category, "other");
});

test("swap forms collect one to five named requested items with amount and unit", () => {
  const app = ui();
  assert.match(app.readDraft().miss.join(" "), /Requested item 1.*add a food name/);
  app.S.draft.wanted_items = [{name:"beans", quantity:"4", unit:"can"}];
  app.S.draft.wanted_quantity = 99;
  const html = app.viewReview();
  assert.match(html, /Items wanted in return/);
  assert.match(html, /data-action="add-wanted-item"/);
  assert.match(html, /Food you'd like/);
  assert.match(html, /Unit/);
  assert.equal(app.readDraft().payload.wanted_items[0].quantity, 4);
  assert.equal(app.readDraft().payload.wanted_items[0].unit, "can");
  assert.equal(app.readDraft().payload.wanted_food, "beans");
  assert.equal(app.readDraft().payload.wanted_quantity, 4);
  assert.doesNotMatch(app.readDraft().miss.join(" "), /Requested item/);
  app.S.draft.wanted_items = Array.from({length: 5}, (_, i) => ({name:`food ${i}`, quantity:"1", unit:"each"}));
  assert.match(app.viewReview(), /data-action="remove-wanted-item"/);
  assert.doesNotMatch(app.viewReview(), /data-action="add-wanted-item"/);
  app.S.draft.wanted_items[2].quantity = "0";
  app.S.draft.wanted_items[3].unit = "";
  app.S.draft.wanted_items[4].name = "";
  assert.match(app.readDraft().miss.join(" "), /whole-number quantity/);
  assert.match(app.readDraft().miss.join(" "), /choose a unit/);
  assert.match(app.readDraft().miss.join(" "), /add a food name/);
  app.S.draft.mode = "giveaway";
  assert.doesNotMatch(app.viewReview(), /Items wanted in return|data-bind="draft.wanted_items/);
  assert.deepEqual(Array.from(app.readDraft().payload.wanted_items), []);
  app.S.draft.items[0].quantity = "100";
  assert.match(app.readDraft().miss.join(" "), /99/);
  app.S.draft.items[0].quantity = "3";
  assert.equal(app.readDraft().payload.item_details[0].quantity, 3);
  assert.equal(app.wantedText({wanted_food: "rice", wanted_unit: "bag", wanted_quantity: 4}), "bags of rice");
  assert.equal(app.wantedText({wanted_items: [
    {name:"rice", quantity:2, unit:"bag"}, {name:"beans", quantity:3, unit:"can"}
  ]}), "2 bags of rice, 3 cans of beans");
});

test("selected swap amounts enforce each matched requested minimum", () => {
  const app = ui(), item = {name:"onions", quantity:5, unit:"each"};
  assert.match(app.swapQuantityFields([item], "minimum", "You offer", [3]), /min="3"/);
  app.nodes.set("#swapqty-minimum-0", {value:"2"});
  assert.match(app.selectedSwapItems([item], "minimum", [3]).error, /from 3 to 5/);
  app.nodes.get("#swapqty-minimum-0").value = "3";
  assert.equal(app.selectedSwapItems([item], "minimum", [3]).items[0].quantity, 3);
});

test("fallback explains the specific requested item blocking a swap", () => {
  const app = ui();
  app.S.fallback = {id:"own", err:{}, fb:{matches:[], options:[], incompatibilities:[
    {items:["Rice"], message:'Quantity mismatch: at least 6 each of "Rice" is requested, but only 5 is available.'}
  ]}};
  const html = app.fallbackBody();
  assert.match(html, /No compatible swap right now/);
  assert.match(html, /Rice: Quantity mismatch/);
  assert.match(html, /at least 6 each/);
});

test("swap acceptance shows full actual amounts for both participants", () => {
  const app = ui();
  const offer = {id: "swap", status: "pending",
    participants: [{user_id: "a", name: "Alice"}, {user_id: "b", name: "Bob"}],
    give_items: [{name: "onions", quantity: 3, unit: "each", requested_minimum: 2, requested_unit: "each"}],
    receive_items: [{name: "cans of beans", quantity: 2, unit: "each", requested_minimum: 1, requested_unit: "can"}],
    location: "Library", pickup_start: new Date(Date.now() + 3600000).toISOString(),
    pickup_end: new Date(Date.now() + 7200000).toISOString()};
  app.S.user = {id: "a"};
  assert.match(app.offerBlock(offer), /you give 3 onions \(requested at least 2 onions\), you get 2 cans of beans \(requested at least 1 can of beans\)/);
  app.S.user = {id: "b"};
  assert.match(app.offerBlock(offer), /you give 2 cans of beans \(requested at least 1 can of beans\), you get 3 onions \(requested at least 2 onions\)/);
});

test("Getting includes swaps with the same handoff controls as Sharing", () => {
  const app = ui();
  app.S.user = {id: "a"};
  app.S.activityLoaded = true;
  app.S.myOffers = [{id: "swap", status: "pending",
    participants: [{user_id: "a", name: "Alice"}, {user_id: "b", name: "Bob"}],
    give_items: [{name: "onions", quantity: 3, unit: "each"}],
    receive_items: [{name: "cans of beans", quantity: 2, unit: "each"}],
    location: "Library", pickup_start: new Date(Date.now() + 3600000).toISOString(),
    pickup_end: new Date(Date.now() + 7200000).toISOString()}];
  app.S.actSeg = "getting";
  let html = app.viewActivity();
  assert.match(html, /Your swaps/);
  assert.match(html, /you give 3 onions, you get 2 cans of beans/);
  assert.match(html, /data-action="offer-respond"/);
  assert.doesNotMatch(html, /Nothing requested yet/);
  app.S.actSeg = "sharing";
  html = app.viewActivity();
  assert.match(html, /you give 3 onions, you get 2 cans of beans/);
  assert.match(html, /data-action="offer-respond"/);
});

test("request form and cards explain optional amounts without inventing counts", () => {
  const app = ui();
  app.openRequestForm();
  const form = app.nodes.get("#sheet").innerHTML;
  assert.match(form, /Quantity \(optional\)/);
  assert.match(form, /placeholder="Any amount"/);
  assert.match(form, /don't reserve food/);
  assert.match(app.requestCard({items: ["onions"], quantity: null}), /Looking for onions · Any amount/);
  assert.match(app.requestCard({items: ["onions"], quantity: 3}), /Looking for 3 onions/);
  assert.doesNotMatch(app.requestCard({items: ["onions"], quantity: 3}), /Any amount/);
});

test("registered request submit sends null for blank, numbers for specified counts and rejects invalid input", async () => {
  const sent = [];
  const app = ui(async (url, options) => {
    sent.push(JSON.parse(options.body));
    // Controlled rejection avoids mutating any real data or mounting Activity.
    return {ok: false, status: 400, json: async () => ({error: "Fixture response"})};
  });
  const now = Date.now(), local = t => limits.toLocalInput(new Date(t));
  const form = {id: "request-form", item: {value: "onions"}, quantity: {value: ""},
    location: {value: "Library steps"}, pickup_start: {value: local(now + 3600000)},
    pickup_end: {value: local(now + 7200000)}, deadline: {value: local(now + 10800000)},
    querySelector() {return {disabled: false};}};
  await app.submit(form);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].quantity, null);
  form.quantity.value = "3";
  await app.submit(form);
  assert.equal(sent.length, 2);
  assert.equal(sent[1].quantity, 3);
  for (const value of ["0", "100", "1.5", "-2"]) {
    form.quantity.value = value;
    await app.submit(form);
    assert.equal(sent.length, 2);
    assert.match(app.nodes.get("#rq-err").innerHTML, /99/);
  }
  form.quantity.value = "";
  form.quantity.validity = {badInput: true};
  await app.submit(form);
  assert.equal(sent.length, 2);
});

test("allergen chips require a choice and keep old free-text declarations untouched", () => {
  const app = ui();
  assert.match(app.readDraft().miss.join(" "), /Select at least one allergen chip/);
  assert.doesNotMatch(app.viewReview(), /id="f-allergens"/);
  for (const label of allergens.ALLERGENS) assert.ok(app.viewReview().includes(`data-value="${label}"`));
  app.S.draft.allergens = "Old label: possible traces of lupin";
  assert.match(app.viewReview(), /Old label: possible traces of lupin/);
  assert.equal(app.readDraft().payload.allergens, "Old label: possible traces of lupin");
  assert.equal(app.readDraft().payload.allergen_choices, undefined);
  assert.match(app.readDraft().miss.join(" "), /Select at least one allergen chip/);
});

test("whole produce defaults to None only on condition changes; user may override", () => {
  const app = ui();
  const change = (path, value) => app.onBind({type: "change",
    target: {type: "text", value, dataset: {bind: path}}});
  change("draft.items.0.condition", "produce");
  assert.equal(app.S.draft.allergens, "None");
  app.actions["allergen-toggle"]({dataset: {value: "Milk"}});
  app.actions["allergen-toggle"]({dataset: {value: "Eggs"}});
  assert.equal(app.S.draft.allergens, "Milk, Eggs");
  change("draft.items.0.name", "apples");
  assert.equal(app.S.draft.allergens, "Milk, Eggs");
  app.actions["allergen-toggle"]({dataset: {value: "Unknown"}});
  assert.equal(app.S.draft.allergens, "Unknown");
  app.actions["allergen-toggle"]({dataset: {value: "Other"}});
  change("draft.allergen_other", "Mustard, lupin");
  assert.equal(app.S.draft.allergens, "Other: Mustard, lupin");
  assert.match(app.viewReview(), /maxlength="50"/);
  change("draft.allergen_other", "");
  assert.match(app.readDraft().miss.join(" "), /Describe the other allergen/);
  assert.equal(app.S.approval, null);
});

test("change-only Clear updates state and does not refill the cleared deadline", () => {
  const app = ui();
  app.S.draft.day = "2026-11-10";
  app.S.draft.until = "16:00";
  app.S.draft.deadline = "2026-11-10T16:00";
  const change = (path, value, type) => app.onBind({
    type: "change", target: { type, value, dataset: { bind: path } },
  });
  change("draft.deadline", "", "datetime-local");
  change("draft.until", "17:00", "time");
  assert.equal(app.S.draft.deadline, "");
  change("draft.day", "", "date");
  assert.equal(app.S.draft.day, "");
  assert.match(app.readDraft().miss.join(" "), /pickup day/);
});

test("registered change handler synchronizes food names and times before Bob validation", () => {
  const app = ui();
  app.S.draft.wanted_items = [{name:"rice", quantity:"1", unit:"each"}];
  const change = (path, value, type = "text") => app.dispatch("change",
    {id: "", type, tagName: "INPUT", value, dataset: {bind: path}});
  assert.ok(app.readDraft().miss.some(m => m.includes("add a food name")));
  change("draft.items.0.name", "onions");
  assert.equal(app.S.draft.items[0].name, "onions");
  assert.ok(!app.readDraft().miss.some(m => m.includes("add a food name")));
  const day = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
  change("draft.day", day, "date");
  change("draft.from", "15:00", "time");
  change("draft.until", "14:00", "time");
  assert.match(app.readDraft().miss.join(" "), /Pickup end needs to be after/);
  change("draft.until", "17:00", "time");
  assert.doesNotMatch(app.readDraft().miss.join(" "), /Pickup end needs to be after/);
  assert.equal(app.readDraft().payload.item_details[0].name, "onions");
  assert.equal(app.readDraft().payload.pickup_end, new Date(`${day}T17:00`).toISOString());
  change("draft.wanted_items.0.name", "cans of beans");
  assert.equal(app.readDraft().payload.wanted_food, "cans of beans");
  change("draft.items.0.name", "");
  assert.ok(app.readDraft().miss.some(m => m.includes("add a food name")));
});

test("duplicate input/change events do not invalidate unchanged Bob approval", () => {
  const app = ui();
  const target = {id: "", type: "text", tagName: "INPUT", value: "onions",
    dataset: {bind: "draft.items.0.name"}};
  app.dispatch("input", target);
  const approved = {id: "approved-current"};
  app.S.approval = approved;
  const gen = app.S.reviewGen;
  app.dispatch("change", target);
  assert.equal(app.S.approval, approved);
  assert.equal(app.S.reviewGen, gen);
  app.dispatch("change", {...target, value: "rice"});
  assert.equal(app.S.approval, null);
  assert.ok(app.S.reviewGen > gen);
});

test("pickup validation reconciles values displayed by native controls", () => {
  const app = ui();
  const start = new Date(Date.now() + 2 * 86400000);
  start.setHours(10, 0, 0, 0);
  const end = new Date(start); end.setHours(11);
  const deadline = new Date(start); deadline.setHours(12);
  const local = limits.toLocalInput;
  app.nodes.set("#f-day", { value: local(start).slice(0, 10) });
  app.nodes.set("#f-from", { value: local(start).slice(11) });
  app.nodes.set("#f-until", { value: local(end).slice(11) });
  app.nodes.set("#f-deadline", { value: local(deadline) });

  const draft = app.readDraft();
  assert.equal(draft.payload.pickup_start, start.toISOString());
  assert.equal(draft.payload.pickup_end, end.toISOString());
  assert.doesNotMatch(draft.miss.join(" "), /Set the pickup (start|end)/);
});

test("pickup fields are free text with exact note, placeholder, limit and counter", () => {
  const app = ui();
  const html = app.spotField("test-spot", "Library steps");
  assert.match(html, /type="text"/);
  assert.match(html, /maxlength="100"/);
  assert.match(html, /13\/100/);
  assert.ok(html.includes(limits.SPOT_PLACEHOLDER));
  assert.ok(html.includes(limits.SPOT_NOTE));
  assert.doesNotMatch(html, /select|option/);
  app.S.draft.location = "Main library north entrance";
  assert.ok(app.viewReview().includes('value="Main library north entrance"'));
  assert.doesNotMatch(app.readDraft().miss.join(" "), /pickup spot/);
  app.S.draft.location = "x".repeat(101);
  assert.match(app.readDraft().miss.join(" "), /100 characters/);
});

test("Discover removes areas filter but retains free-text location cards", () => {
  const app = ui();
  app.S.loaded = true;
  app.S.listings = [{
    id: "one", mode: "giveaway", status: "open", location: "Main library front steps",
    item_details: [{ name: "onions", quantity: 3, unit: "each" }],
    deadline: new Date(Date.now() + 86400000).toISOString(),
  }];
  const html = app.viewDiscover();
  assert.doesNotMatch(html, /All areas|open-zone|set-zone/);
  assert.match(html, /Main library front steps/);
});

test("request sheet uses listing spot and Activity uses chosen request time", () => {
  const app = ui();
  const now = Date.now();
  const listing = { id: "one", name: "Owner", location: "North door by the library",
    pickup_start: new Date(now + 3600000).toISOString(),
    pickup_end: new Date(now + 4 * 3600000).toISOString(),
    deadline: new Date(now + 5 * 3600000).toISOString() };
  const form = app.giveawayBody(listing);
  assert.match(form, /North door by the library/);
  assert.match(form, /name="location" readonly/);
  assert.doesNotMatch(form, /<select id="g-zone"/);
  app.S.user = { id: "owner" };
  const row = { id: "r", owner_id: "owner", requester_id: "other", requester_name: "Hyun",
    status: "approved", location: listing.location, item_details: [],
    pickup_start: new Date(now + 2 * 3600000).toISOString(),
    pickup_end: new Date(now + 3 * 3600000).toISOString() };
  const expected = new Date(row.pickup_start).toLocaleString(undefined,
    { weekday: "short", hour: "numeric", minute: "2-digit" });
  assert.ok(app.giveawayBlock(row).includes(`Hyun will pick up ${expected}`));
  app.S.user = { id: "other" };
  assert.ok(app.giveawayBlock(row).includes(`You will pick up ${expected}`));
});
