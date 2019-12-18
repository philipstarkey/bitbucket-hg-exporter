'use strict';

angular.
  module('BitbucketBackupApp').
  config(['$routeProvider',
    function config($routeProvider) {
      $routeProvider.
        when('/', {
          template: '<repo-list></repo-list>'
        }).
        when('/:owner/:project', {
          template: '<index-page></index-page>'
        }).
        // Issues list
        when('/:owner/:project/issues', {
          redirectTo: '/:owner/:project/issues/page/1'
        }).
        when('/:owner/:project/issues/page/:pageId', {
          template: '<issues-list></issues-list>'
        }).
        // Issue details
        when('/:owner/:project/issues/:issueId/page/:pageId', {
          template: '<issue-details></issue-details>',
          reloadOnSearch: false,
        }).
        when('/:owner/:project/issues/:issueId', {
          redirectTo: '/:owner/:project/issues/:issueId/page/1'
        }).
        when('/:owner/:project/issues/:issueId/:slug', {
          redirectTo: '/:owner/:project/issues/:issueId/page/1'
        }).
        when('/:owner/:project/issue/:issueId', {
          redirectTo: '/:owner/:project/issues/:issueId/page/1'
        }).
        when('/:owner/:project/issue/:issueId/page/:pageId', {
          redirectTo: '/:owner/:project/issues/:issueId/page/:pageId'
        }).
        when('/:owner/:project/issue/:issueId/:slug', {
          redirectTo: '/:owner/:project/issues/:issueId/page/1'
        }).
        // pull requests list
        when('/:owner/:project/pull-requests/page/:pageId', {
          template: '<pullrequests-list></pullrequests-list>',
          reloadOnSearch: false,
        }).
        when('/:owner/:project/pull-requests', {
          redirectTo: '/:owner/:project/pull-requests/page/1'
        }).
        // pull requests details
        when('/:owner/:project/pull-requests/:prId/page/:pageId', {
          template: '<pullrequest-details></pullrequest-details>',
          reloadOnSearch: false,
        }).
        when('/:owner/:project/pull-requests/:prId', {
          redirectTo: '/:owner/:project/pull-requests/:prId/page/1'
        }).
        when('/:owner/:project/pull-requests/:prId/:slug', {
          redirectTo: '/:owner/:project/pull-requests/:prId/page/1'
        }).
        // commit details
        when('/:owner/:project/commits/:commitSlug/page/:pageId', {
          template: '<commit-details></commit-details>',
          reloadOnSearch: false,
        }).
        when('/:owner/:project/commits/:commitSlug', {
          redirectTo: '/:owner/:project/commits/:commitSlug/page/1'
        }).
        otherwise('/');
    }
  ]);