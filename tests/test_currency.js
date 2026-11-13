const assert = require('node:assert/strict')
const {readFileSync} = require('node:fs')
const {join} = require('node:path')
const {test} = require('node:test')
const vm = require('node:vm')

// Exercise the actual Vue methods without a live wallet or external requests.
const template = readFileSync(
  join(__dirname, '../templates/boltz/index.html'),
  'utf8'
)
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1]

function createApp(request = async () => ({data: {sats: 50000}})) {
  let options
  const errors = []
  const notices = []
  const swaps = []
  vm.runInNewContext(script, {
    window: {},
    Vue: {createApp: value => (options = value)},
    LNbits: {
      api: {request},
      utils: {notifyApiError: error => errors.push(error)}
    },
    _: {findWhere: (items, query) => items.find(item => item.id === query.id)}
  })
  const app = {
    ...options.data(),
    g: {
      user: {wallets: [{id: 'wallet'}]},
      allowedCurrencies: ['GBP'],
      currencies: ['GBP', 'USD']
    },
    $q: {notify: notice => notices.push(notice)}
  }
  for (const [name, method] of Object.entries(options.methods)) {
    app[name] = method.bind(app)
  }
  app.boltzConfig = {'BTC/BTC': {limits: {minimal: 10000, maximal: 200000}}}
  for (const type of ['submarine', 'reverse']) {
    const dialog =
      type === 'submarine'
        ? app.submarineSwapDialog
        : app.reverseSubmarineSwapDialog
    Object.assign(dialog.data, {
      wallet: 'wallet',
      amount: 50,
      currency: 'GBP',
      refund_address: 'refund',
      onchain_address: 'destination'
    })
  }
  app.createSubmarineSwap = async (wallet, data) => swaps.push({wallet, data})
  app.createReverseSubmarineSwap = async (wallet, data) =>
    swaps.push({wallet, data})
  return {app, errors, notices, swaps}
}

test('currency selection respects the Core allowlist and handles older globals', () => {
  const {app} = createApp()
  app.loadCurrencies()
  assert.deepEqual(Array.from(app.currencies), ['sats', 'GBP'])
  delete app.g.allowedCurrencies
  app.loadCurrencies()
  assert.deepEqual(Array.from(app.currencies), ['sats', 'GBP', 'USD'])
  delete app.g.currencies
  app.loadCurrencies()
  assert.deepEqual(Array.from(app.currencies), ['sats'])
})

for (const type of ['submarine', 'reverse']) {
  test(`${type} compares fiat amounts with sats limits after conversion`, () => {
    const {app} = createApp()
    const disabled =
      type === 'submarine'
        ? app.disableSubmarineSwapDialog
        : app.disableReverseSubmarineSwapDialog
    const data =
      type === 'submarine'
        ? app.submarineSwapDialog.data
        : app.reverseSubmarineSwapDialog.data
    assert.equal(disabled(), true)
    app.fiatRates.GBP = 1000
    assert.equal(disabled(), false) // £50 is 50,000 sats, within the limits.
    data.amount = 9.99
    assert.equal(disabled(), true)
    data.amount = 10
    assert.equal(disabled(), false)
    data.amount = 200
    assert.equal(disabled(), false)
    data.amount = 200.01
    assert.equal(disabled(), true)
  })

  test(`${type} uses Core conversion at submission and preserves the fiat amount`, async () => {
    const requests = []
    const {app, swaps} = createApp(async (...args) => {
      requests.push(args)
      return {data: {sats: 51000}}
    })
    app.fiatRates.GBP = 1000
    await app.sendSwapFormData(type)
    assert.equal(requests.length, 1)
    assert.equal(requests[0][0], 'POST')
    assert.equal(requests[0][1], '/api/v1/conversion')
    assert.equal(requests[0][3].from_, 'GBP')
    assert.equal(requests[0][3].amount, 50)
    assert.equal(swaps.length, 1)
    assert.equal(swaps[0].data.amount, 51000)
    assert.equal(swaps[0].data.amount_display, 50)
    assert.equal(swaps[0].data.currency, 'GBP')
    assert.equal(app.swapSubmitting[type], false)
  })

  test(`${type} blocks an out-of-range refreshed amount`, async () => {
    const {app, swaps, notices} = createApp(async () => ({data: {sats: 9999}}))
    app.fiatRates.GBP = 1000
    await app.sendSwapFormData(type)
    assert.equal(swaps.length, 0)
    assert.equal(notices.length, 1)
    assert.equal(app.swapSubmitting[type], false)
  })

  test(`${type} fails without creating a swap when conversion is unavailable`, async () => {
    const {app, swaps, errors} = createApp(async () => {
      throw new Error('rate unavailable')
    })
    app.fiatRates.GBP = 1000
    await app.sendSwapFormData(type)
    assert.equal(swaps.length, 0)
    assert.equal(errors.length, 1)
    assert.equal(app.swapSubmitting[type], false)
  })

  test(`${type} snapshots form values and blocks double submission while converting`, async () => {
    let finish
    let calls = 0
    const {app, swaps} = createApp(() => {
      calls++
      return new Promise(resolve => (finish = resolve))
    })
    app.fiatRates.GBP = 1000
    const pending = app.sendSwapFormData(type)
    const data =
      type === 'submarine'
        ? app.submarineSwapDialog.data
        : app.reverseSubmarineSwapDialog.data
    data.amount = 80
    data.currency = 'USD'
    data.onchain_address = 'other-destination'
    await app.sendSwapFormData(type)
    assert.equal(calls, 1)
    finish({data: {sats: 50000}})
    await pending
    assert.equal(swaps.length, 1)
    assert.equal(swaps[0].data.amount_display, 50)
    assert.equal(swaps[0].data.currency, 'GBP')
    assert.equal(swaps[0].data.onchain_address, 'destination')
  })
}

test('sats do not require a fiat request and fractional or invalid sats are blocked', async () => {
  const {app, swaps} = createApp(() => {
    throw new Error('sats must not query an exchange rate')
  })
  const data = app.submarineSwapDialog.data
  data.currency = 'sats'
  for (const amount of [NaN, Infinity, -1, 0, 50000.5]) {
    data.amount = amount
    assert.equal(app.disableSubmarineSwapDialog(), true)
    assert.doesNotThrow(() => app.getAmountHint('submarine'))
  }
  data.amount = 50000
  await app.sendSubmarineSwapFormData()
  assert.equal(swaps.length, 1)
  assert.equal(swaps[0].data.amount, 50000)
})

test('invalid or failed preview rates clear stale rates and keep fiat submission disabled', async () => {
  for (const rate of [0, -100, Infinity, NaN, '1000']) {
    const {app, errors} = createApp(async () => ({data: {rate}}))
    app.fiatRates.GBP = 1000
    await app.updateFiatRate('GBP')
    assert.equal(app.fiatRates.GBP, undefined)
    assert.equal(app.disableSubmarineSwapDialog(), true)
    assert.equal(errors.length, 1)
    assert.match(app.getAmountHint('submarine'), /unavailable/)
  }
})

test('preview rounding matches Core truncation', () => {
  const {app} = createApp()
  app.fiatRates.GBP = 1000.019
  assert.equal(app.convertFiatToSats(50, 'GBP'), 50000)
})
