/**
 * 用于 API Promise 化
 */

function promisify() {
  if (tt.promises) {
    return;
  }

  tt.promises = new Proxy(tt, {
    get(obj, api) {
      const fn = obj[api];
      const isFunction = typeof fn === 'function';
      const isSyncFunction = api.endsWith('Sync');
      const isEventFunction = api.startsWith('on') || api.startsWith('off');
      const isCreateFunction = api.startsWith('create');

      if (!isFunction || isSyncFunction || isEventFunction || isCreateFunction) {
        return fn.bind(obj);
      }

      return params => {
        return new Promise((resolve, reject) => {
          fn.call(obj, {
            ...params,
            success: resolve,
            fail: reject,
            complete() { },
          });
        });
      };
    }
  });
}

export default promisify;
