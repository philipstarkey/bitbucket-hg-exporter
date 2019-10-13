'use strict';

// Define the `phonecatApp` module
var app = angular.module('BitbucketBackupApp', [
  'ui.bootstrap',
  'ngRoute',
  'issuesList',
  'issueDetails',
  'indexPage',
  'sidebarLinks'
]);

app.run(function($rootScope, $http) {
  $rootScope.project_name = 'empty';
  $rootScope.relative_project_url = 'data/repositories/philipstarkey/qtutils/';
  $http.get($rootScope.relative_project_url + '../qtutils.json').then(function(response) {
    $rootScope.project_name = response.data['name'];
    $rootScope.project_data = response.data;

    $rootScope.links = [
      {text: 'Home', url:'#!/'},
      {text: 'Issues', url:'#!/issues'},
    ];
  });


})