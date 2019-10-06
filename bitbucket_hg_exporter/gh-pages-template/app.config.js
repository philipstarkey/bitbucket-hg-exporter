'use strict';

angular.
  module('BitbucketBackupApp').
  config(['$routeProvider',
    function config($routeProvider) {
      $routeProvider.
        when('/', {
          template: '<index-page></index-page>'
        }).
        when('/issues/page=:pageId', {
          template: '<issues-list></issues-list>'
        }).
        when('/issue/:issueId', {
          template: '<issue-details></issue-details>'
        }).
        when('/sidebartest', {
          template: '<sidebar-links></sidebar-links>'
        }).
        otherwise('/');
    }
  ]);